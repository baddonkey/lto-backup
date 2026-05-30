"""Integration test for a full simulated backup → restore flow."""

import hashlib
from pathlib import Path

from lto_backup.config.backup_config import BackupConfig
from lto_backup.infrastructure.filesystem.local_file_system import LocalFileSystem
from lto_backup.infrastructure.filesystem.sha256_file_hasher import Sha256FileHasher
from lto_backup.infrastructure.simulator.simulator_tape_drive import SimulatorTapeDrive
from lto_backup.services.restore_service import RestoreService
from lto_backup.services.tape_switch_service import TapeSwitchService
from lto_backup.infrastructure.catalog.json_catalog_serializer import JsonCatalogSerializer
from lto_backup.wiring.container import build_backup_service, build_restore_service

_TAPE_NOMINAL = 10_000


def _config(source: Path, tapes: Path, capacity: int = _TAPE_NOMINAL) -> BackupConfig:
    return BackupConfig(
        source_root=source,
        tapes_root=tapes,
        tape_nominal_capacity_bytes=capacity,
        max_container_size_bytes=capacity,
    )


class AutoloadTapeSwitchService:
    """TapeSwitchService replacement that loads tapes without operator interaction."""

    def __init__(self, tape_drive: SimulatorTapeDrive) -> None:
        self._tape_drive = tape_drive

    def request_and_load(self, tape_id: str, sequence_number: int) -> None:
        self._tape_drive.load_tape(tape_id)


def _restore_service(tapes: Path, capacity: int = _TAPE_NOMINAL) -> RestoreService:
    """Build a RestoreService wired to the simulator with no operator prompts."""
    tape_drive = SimulatorTapeDrive(tapes, capacity)
    return RestoreService(
        tape_drive=tape_drive,
        tape_switch_service=AutoloadTapeSwitchService(tape_drive),  # type: ignore[arg-type]
        serializer=JsonCatalogSerializer(),
        file_hasher=Sha256FileHasher(),
        file_system=LocalFileSystem(),
    )


class TestSimulatedRestoreFlow:
    """End-to-end backup → restore scenarios using SimulatorTapeDrive."""

    # ------------------------------------------------------------------
    # Test 1 — single file, single tape
    # ------------------------------------------------------------------

    def test_restore_single_file_single_tape(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        restored = tmp_path / "restored"
        source.mkdir()
        tapes.mkdir()
        (source / "file.bin").write_bytes(b"x" * 200)

        catalog = build_backup_service(_config(source, tapes)).run(
            _config(source, tapes)
        )
        report = _restore_service(tapes).restore(catalog, restore_root=restored)

        assert report.errors == []
        assert report.files_restored == 1
        assert (restored / "file.bin").read_bytes() == b"x" * 200

    # ------------------------------------------------------------------
    # Test 2 — multiple files, single tape
    # ------------------------------------------------------------------

    def test_restore_multiple_files_single_tape(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        restored = tmp_path / "restored"
        source.mkdir()
        tapes.mkdir()
        files = {f"file{i}.bin": bytes([i]) * 50 for i in range(3)}
        for name, data in files.items():
            (source / name).write_bytes(data)

        catalog = build_backup_service(_config(source, tapes)).run(
            _config(source, tapes)
        )
        report = _restore_service(tapes).restore(catalog, restore_root=restored)

        assert report.errors == []
        assert report.files_restored == 3
        for name, expected in files.items():
            assert (restored / name).read_bytes() == expected

    # ------------------------------------------------------------------
    # Test 3 — file spanning two tapes, SHA-256 matches
    # ------------------------------------------------------------------

    def test_restore_file_spanning_two_tapes(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        restored = tmp_path / "restored"
        source.mkdir()
        tapes.mkdir()
        # file1 is larger than usable capacity per tape (nominal=10_000, catalog ≈ 1 KB)
        data1 = b"A" * 8_500
        data2 = b"B" * 100
        (source / "large.bin").write_bytes(data1)
        (source / "small.bin").write_bytes(data2)

        catalog = build_backup_service(_config(source, tapes)).run(
            _config(source, tapes)
        )
        assert len(catalog.tapes) == 2, "Pre-condition: backup must use two tapes"

        report = _restore_service(tapes).restore(catalog, restore_root=restored)

        assert report.errors == []
        assert report.files_restored == 2
        assert (restored / "large.bin").read_bytes() == data1
        assert (restored / "small.bin").read_bytes() == data2

    # ------------------------------------------------------------------
    # Test 4 — all restored files match catalog SHA-256
    # ------------------------------------------------------------------

    def test_restored_files_match_catalog_sha256(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        restored = tmp_path / "restored"
        source.mkdir()
        tapes.mkdir()
        for i in range(4):
            content = bytes(range(256)) * (i + 1)
            (source / f"data{i}.bin").write_bytes(content)

        catalog = build_backup_service(_config(source, tapes)).run(
            _config(source, tapes)
        )
        report = _restore_service(tapes).restore(catalog, restore_root=restored)

        assert report.errors == []
        hasher = Sha256FileHasher()
        for sf in catalog.source_files:
            dest = restored / sf.relative_path
            assert hasher.hash_file(dest) == sf.sha256

    # ------------------------------------------------------------------
    # Test 5 — filter glob: only matching files restored
    # ------------------------------------------------------------------

    def test_restore_filter_glob(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        restored = tmp_path / "restored"
        source.mkdir()
        tapes.mkdir()
        (source / "report.txt").write_bytes(b"text")
        (source / "video.bin").write_bytes(b"binary")

        catalog = build_backup_service(_config(source, tapes)).run(
            _config(source, tapes)
        )
        report = _restore_service(tapes).restore(
            catalog, restore_root=restored, filter_glob="*.txt"
        )

        assert report.files_requested == 1
        assert report.files_restored == 1
        assert (restored / "report.txt").exists()
        assert not (restored / "video.bin").exists()

    # ------------------------------------------------------------------
    # Test 6 — load_catalog_from_tape returns a valid catalog
    # ------------------------------------------------------------------

    def test_load_catalog_from_tape(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        source.mkdir()
        tapes.mkdir()
        (source / "file.bin").write_bytes(b"data")

        written_catalog = build_backup_service(_config(source, tapes)).run(
            _config(source, tapes)
        )
        first_tape_id = written_catalog.tapes[0].tape_id

        loaded_catalog = _restore_service(tapes).load_catalog_from_tape(first_tape_id)

        assert loaded_catalog.backup_set_id == written_catalog.backup_set_id
        assert len(loaded_catalog.source_files) == 1

    # ------------------------------------------------------------------
    # Test 7 — zero-byte files are restored as empty files
    # ------------------------------------------------------------------

    def test_restore_zero_byte_file(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        tapes = tmp_path / "tapes"
        restored = tmp_path / "restored"
        source.mkdir()
        tapes.mkdir()
        (source / "nonempty.bin").write_bytes(b"content")
        (source / "empty.log").write_bytes(b"")

        catalog = build_backup_service(_config(source, tapes)).run(
            _config(source, tapes)
        )
        report = _restore_service(tapes).restore(catalog, restore_root=restored)

        assert report.errors == []
        assert report.files_restored == 2
        assert (restored / "empty.log").exists()
        assert (restored / "empty.log").read_bytes() == b""
