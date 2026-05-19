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
        max_container_size_bytes=capacity,  # clamped to usable_capacity by planner
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
        # file1 is larger than any possible single-tape usable capacity
        # (catalog overhead is well under 2000 bytes for a 2-file backup),
        # so the planner must allocate a second tape for file2.
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        source.mkdir()
        tapes.mkdir()

        (source / "file1.bin").write_bytes(b"A" * 8_500)
        (source / "file2.bin").write_bytes(b"B" * 100)

        catalog = build_backup_service(_config(source, tapes)).run(
            _config(source, tapes)
        )

        assert len(catalog.tapes) == 2
        assert len(catalog.source_files) == 2

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
        # 9 000-byte file on a 10 000-byte tape: catalog reserve (~2 600 bytes)
        # leaves ~7 400 bytes usable, so the file must span two tapes.
        (source / "big.bin").write_bytes(b"Y" * 9_000)
        config = _config(source, tapes, capacity=10_000)

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
        (source / "big.bin").write_bytes(b"Y" * 9_000)
        config = _config(source, tapes, capacity=10_000)

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
        (source / "big.bin").write_bytes(b"Y" * 9_000)
        config = _config(source, tapes, capacity=10_000)

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
        # 9 000-byte file on a 10 000-byte tape guarantees at least one split.
        (source / "big.bin").write_bytes(b"Y" * 9_000)
        config = _config(source, tapes, capacity=10_000)

        catalog = build_backup_service(config).run(config)

        assert len(catalog.tapes) >= 2
        assert len(catalog.segments) >= 2
        # Every segment must have a non-empty sha256 set by the writer.
        for seg in catalog.segments:
            assert seg.sha256 != "", f"segment {seg.segment_id} has empty sha256"

        errors = _verifier(tapes, capacity=10_000).verify(catalog)
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

        # Overwrite the first container's data file on the virtual tape.
        tape_id = catalog.tapes[0].tape_id
        container_id = next(c.container_id for c in catalog.containers if c.tape_id == tape_id)
        (tapes / tape_id / "data" / container_id).write_bytes(b"CORRUPTED DATA")

        errors = _verifier(tapes).verify(catalog)

        assert len(errors) > 0
