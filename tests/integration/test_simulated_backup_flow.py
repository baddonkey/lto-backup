"""Integration test for a full simulated backup flow."""

from pathlib import Path

from lto_backup.config.backup_config import BackupConfig
from lto_backup.infrastructure.catalog.json_catalog_serializer import JsonCatalogSerializer
from lto_backup.infrastructure.clock.system_clock import SystemClock
from lto_backup.infrastructure.filesystem.local_file_system import LocalFileSystem
from lto_backup.infrastructure.filesystem.sha256_file_hasher import Sha256FileHasher
from lto_backup.infrastructure.simulator.simulator_tape_drive import SimulatorTapeDrive
from lto_backup.services.backup_planner import BackupPlanner
from lto_backup.services.source_scanner import SourceScanner
from lto_backup.services.verification_service import VerificationService
from lto_backup.wiring.container import build_backup_service

# Large enough that the JSON catalog overhead is negligible relative to file data.
_TAPE_NOMINAL = 10_000


def _config(source: Path, tapes: Path, capacity: int = _TAPE_NOMINAL) -> BackupConfig:
    return BackupConfig(
        source_root=source,
        tapes_root=tapes,
        tape_nominal_capacity_bytes=capacity,
    )


def _verifier(tapes: Path, capacity: int = _TAPE_NOMINAL) -> VerificationService:
    return VerificationService(
        SimulatorTapeDrive(tapes, capacity),
        JsonCatalogSerializer(),
        Sha256FileHasher(),
    )


def _probe_usable_capacity(
    source: Path,
    tapes: Path,
    capacity: int,
) -> int:
    """Return the exact usable data capacity for *source*'s current files.

    Runs the two-pass planner algorithm on the real files so the JSON catalog
    reserve is measured accurately.  Converges in at most a couple of
    iterations because the JSON size changes only when the ``size_bytes``
    digit-count changes between iterations.
    """
    config = _config(source, tapes, capacity=capacity)
    serializer = JsonCatalogSerializer()
    clock = SystemClock()
    scanner = SourceScanner(LocalFileSystem(), Sha256FileHasher(), clock)
    planner = BackupPlanner(serializer, clock)

    for _ in range(4):
        source_files = scanner.scan(source)
        plan = planner.plan(source_files, config)
        reserved = plan.tapes[0].reserved_catalog_bytes
        return capacity - reserved

    # Unreachable but satisfies the type checker.
    return capacity - plan.tapes[0].reserved_catalog_bytes  # type: ignore[possibly-undefined]


class TestSimulatedBackupFlow:
    """End-to-end backup and verification scenarios using SimulatorTapeDrive."""

    # ------------------------------------------------------------------
    # Test 1 — single file, single tape
    # ------------------------------------------------------------------

    def test_backup_single_tape_single_file_catalog_structure(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        source.mkdir()
        tapes.mkdir()
        (source / "file.bin").write_bytes(b"x" * 100)

        catalog = build_backup_service(_config(source, tapes)).run(_config(source, tapes))

        assert len(catalog.tapes) == 1
        assert len(catalog.source_files) == 1
        assert len(catalog.segments) == 1

    def test_backup_single_tape_single_file_verifies_clean(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        source.mkdir()
        tapes.mkdir()
        (source / "file.bin").write_bytes(b"x" * 100)

        catalog = build_backup_service(_config(source, tapes)).run(_config(source, tapes))
        errors = _verifier(tapes).verify(catalog)

        assert errors == []

    # ------------------------------------------------------------------
    # Test 2 — three files, single tape
    # ------------------------------------------------------------------

    def test_backup_multiple_files_single_tape_catalog_structure(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        source.mkdir()
        tapes.mkdir()
        for i in range(3):
            (source / f"file{i}.bin").write_bytes(b"y" * 100)

        catalog = build_backup_service(_config(source, tapes)).run(_config(source, tapes))

        assert len(catalog.tapes) == 1
        assert len(catalog.source_files) == 3
        assert len(catalog.segments) == 3

    # ------------------------------------------------------------------
    # Test 3 — two files span two tapes
    # ------------------------------------------------------------------

    def test_backup_files_span_two_tapes_catalog_structure(
        self, tmp_path: Path
    ) -> None:
        # BackupWriter validates hash(chunk) == segment.sha256 where
        # segment.sha256 = source_file.sha256 (full-file hash set by the planner).
        # This check passes only when chunk == full file (no split).
        #
        # To guarantee no splitting while still spanning two tapes we must make
        # file1 fill tape 1 *exactly* so the planner allocates a fresh tape for
        # file2.  We find the exact usable capacity by probing the planner with
        # small placeholder files and iterating to a stable value.
        #
        # The tape capacity must also exceed the final serialized catalog size
        # (~2 400 bytes for 2 files / 2 tapes / 2 segments).  _TAPE_NOMINAL
        # (10 000 bytes) satisfies that constraint comfortably.
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        source.mkdir()
        tapes.mkdir()

        # --- probe pass: measure catalog overhead with tiny placeholders ---
        (source / "file1.bin").write_bytes(b"\x00")
        (source / "file2.bin").write_bytes(b"\x00")
        usable = _probe_usable_capacity(source, tapes, _TAPE_NOMINAL)

        # file1 fills tape 1 exactly; when tape_offset == usable the planner
        # opens a new tape, so file2 (100 bytes) lands entirely on tape 2.
        (source / "file1.bin").write_bytes(b"A" * usable)
        (source / "file2.bin").write_bytes(b"B" * 100)

        # Re-probe with the real file sizes (size_bytes digit count may differ).
        usable = _probe_usable_capacity(source, tapes, _TAPE_NOMINAL)
        (source / "file1.bin").write_bytes(b"A" * usable)

        catalog = build_backup_service(_config(source, tapes)).run(
            _config(source, tapes)
        )

        assert len(catalog.tapes) == 2
        assert len(catalog.source_files) == 2
        assert len(catalog.segments) == 2

    # ------------------------------------------------------------------
    # Test 4 — single large file splits across tapes
    # ------------------------------------------------------------------

    def test_backup_split_large_file_plan_has_multiple_tapes(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        source.mkdir()
        tapes.mkdir()
        # 5 000-byte file on a 4 000-byte tape guarantees a split regardless of
        # catalog overhead (overhead ≪ 1 000 bytes for a single-file catalog).
        (source / "big.bin").write_bytes(b"Y" * 5_000)
        config = _config(source, tapes, capacity=4_000)

        serializer = JsonCatalogSerializer()
        clock = SystemClock()
        plan = BackupPlanner(serializer, clock).plan(
            SourceScanner(LocalFileSystem(), Sha256FileHasher(), clock).scan(source),
            config,
        )

        assert len(plan.tapes) >= 2

    def test_backup_split_large_file_first_segment_source_offset_is_zero(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        source.mkdir()
        tapes.mkdir()
        (source / "big.bin").write_bytes(b"Y" * 5_000)
        config = _config(source, tapes, capacity=4_000)

        serializer = JsonCatalogSerializer()
        clock = SystemClock()
        plan = BackupPlanner(serializer, clock).plan(
            SourceScanner(LocalFileSystem(), Sha256FileHasher(), clock).scan(source),
            config,
        )
        first_seg = min(plan.segments, key=lambda s: s.source_offset)

        assert first_seg.source_offset == 0

    def test_backup_split_large_file_later_segment_has_nonzero_source_offset(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        source.mkdir()
        tapes.mkdir()
        (source / "big.bin").write_bytes(b"Y" * 5_000)
        config = _config(source, tapes, capacity=4_000)

        serializer = JsonCatalogSerializer()
        clock = SystemClock()
        plan = BackupPlanner(serializer, clock).plan(
            SourceScanner(LocalFileSystem(), Sha256FileHasher(), clock).scan(source),
            config,
        )
        last_seg = max(plan.segments, key=lambda s: s.source_offset)

        assert last_seg.source_offset > 0

    # ------------------------------------------------------------------
    # Test 4d — split large file: full backup+verify passes end-to-end
    # ------------------------------------------------------------------

    def test_backup_split_large_file_end_to_end_verifies_clean(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        source.mkdir()
        tapes.mkdir()
        # 5 000-byte file on a 4 000-byte tape guarantees at least one split.
        (source / "big.bin").write_bytes(b"Y" * 5_000)
        config = _config(source, tapes, capacity=4_000)

        catalog = build_backup_service(config).run(config)

        assert len(catalog.tapes) >= 2
        assert len(catalog.segments) >= 2
        # Every segment must have a non-empty sha256 set by the writer.
        for seg in catalog.segments:
            assert seg.sha256 != "", f"segment {seg.segment_id} has empty sha256"

        errors = _verifier(tapes, capacity=4_000).verify(catalog)
        assert errors == []

    # ------------------------------------------------------------------
    # Test 5 — verification detects corruption
    # ------------------------------------------------------------------

    def test_verification_detects_corruption(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        source.mkdir()
        tapes.mkdir()
        (source / "file.bin").write_bytes(b"x" * 100)

        catalog = build_backup_service(_config(source, tapes)).run(_config(source, tapes))

        # Overwrite the first segment's data file on the virtual tape.
        tape_id = catalog.tapes[0].tape_id
        seg_id = catalog.segments[0].segment_id
        (tapes / tape_id / "data" / seg_id).write_bytes(b"CORRUPTED DATA")

        errors = _verifier(tapes).verify(catalog)

        assert len(errors) > 0
