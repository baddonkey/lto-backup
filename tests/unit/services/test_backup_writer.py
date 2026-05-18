"""Unit tests for BackupWriter service."""

import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from lto_backup.domain.backup_plan import BackupPlan
from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape import Tape
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.exceptions.backup_plan_error import BackupPlanError
from lto_backup.exceptions.file_write_error import FileWriteError
from lto_backup.exceptions.source_file_changed_error import SourceFileChangedError
from lto_backup.exceptions.tape_full_error import TapeFullError
from lto_backup.exceptions.tape_not_loaded_error import TapeNotLoadedError
from lto_backup.services.backup_writer import BackupWriter


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeTapeDrive:
    """In-memory tape drive that records calls and written data."""

    def __init__(self) -> None:
        self.loaded_tape_id: str | None = None
        self.load_calls: list[str] = []
        self.unload_calls: int = 0
        self.written: dict[str, bytes] = {}
        self._raise_tape_full_for: set[str] = set()
        self._raise_not_loaded_for: set[str] = set()

    def raise_tape_full_on(self, destination_name: str) -> None:
        self._raise_tape_full_for.add(destination_name)

    def raise_not_loaded_on(self, tape_id: str) -> None:
        self._raise_not_loaded_for.add(tape_id)

    def load_tape(self, tape_id: str) -> None:
        if tape_id in self._raise_not_loaded_for:
            raise TapeNotLoadedError(f"Cannot load tape {tape_id!r}.")
        self.loaded_tape_id = tape_id
        self.load_calls.append(tape_id)

    def unload_tape(self) -> None:
        self.loaded_tape_id = None
        self.unload_calls += 1

    def current_tape_id(self) -> str:
        assert self.loaded_tape_id is not None
        return self.loaded_tape_id

    def remaining_capacity_bytes(self) -> int:
        return 2**40

    def write_file(self, source_path: Path, destination_name: str) -> None:
        raise NotImplementedError

    def write_bytes(self, destination_name: str, data: bytes) -> None:
        if destination_name in self._raise_tape_full_for:
            raise TapeFullError(f"Tape full writing {destination_name!r}.")
        self.written[destination_name] = data

    def read_file(self, name: str) -> bytes:
        return self.written[name]

    def list_files(self) -> list[str]:
        return list(self.written.keys())


class FakeFileSystem:
    """Returns pre-registered byte content for file paths."""

    def __init__(self) -> None:
        self._files: dict[Path, bytes] = {}

    def register(self, path: Path, data: bytes) -> None:
        self._files[path] = data

    def list_files(self, root: Path) -> list[Path]:
        raise NotImplementedError

    def file_size(self, path: Path) -> int:
        return len(self._files[path])

    def modified_at_timestamp(self, path: Path) -> float:
        return 0.0

    def open_for_read(self, path: Path) -> bytes:
        return self._files[path]


class FakeFileHasher:
    """Computes real SHA-256 so segment checksums are correct by default."""

    def hash_file(self, path: Path) -> str:
        raise NotImplementedError

    def hash_bytes(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_tape(tape_id: str, seq: int = 1) -> Tape:
    return Tape(
        tape_id=tape_id,
        backup_set_id="BS-1",
        sequence_number=seq,
        nominal_capacity_bytes=1_000_000,
        reserved_catalog_bytes=1000,
    )


def _make_file(file_id: str, data: bytes) -> SourceFile:
    return SourceFile(
        file_id=file_id,
        relative_path=f"path/{file_id}",
        absolute_path=f"/src/path/{file_id}",
        size_bytes=len(data),
        sha256=_sha256(data),
        modified_at=datetime(2026, 1, 1),
    )


def _make_segment(
    seg_id: str,
    file_id: str,
    tape_id: str,
    data: bytes,
    source_offset: int = 0,
    tape_offset: int = 0,
) -> TapeSegment:
    return TapeSegment(
        segment_id=seg_id,
        file_id=file_id,
        tape_id=tape_id,
        tape_offset=tape_offset,
        source_offset=source_offset,
        length_bytes=len(data),
        sha256="",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBackupWriter:
    """Tests for BackupWriter.write()."""

    def setup_method(self) -> None:
        self.tape_drive = FakeTapeDrive()
        self.file_system = FakeFileSystem()
        self.file_hasher = FakeFileHasher()
        self.writer = BackupWriter(self.tape_drive, self.file_system, self.file_hasher)

    # Test 1 — single file on single tape
    def test_single_file_single_tape_written_correctly(self) -> None:
        data = b"hello backup"
        tape = _make_tape("TAPE-1")
        sf = _make_file("f1", data)
        segment = _make_segment("SEG-f1-001", "f1", "TAPE-1", data)
        plan = BackupPlan(
            backup_set_id="BS-1",
            source_root="/src",
            tapes=[tape],
            source_files=[sf],
            segments=[segment],
        )
        self.file_system.register(Path("/src/path/f1"), data)

        self.writer.write(plan)

        assert self.tape_drive.written["SEG-f1-001"] == data

    # Test 2a — split file: correct first chunk written to tape 1
    def test_split_file_first_chunk_written_to_tape_one(self) -> None:
        data = b"A" * 100 + b"B" * 100
        tape1 = _make_tape("TAPE-1", seq=1)
        tape2 = _make_tape("TAPE-2", seq=2)
        sf = _make_file("f1", data)
        chunk1 = data[:100]
        chunk2 = data[100:]
        seg1 = _make_segment("SEG-f1-001", "f1", "TAPE-1", chunk1, source_offset=0)
        seg2 = _make_segment("SEG-f1-002", "f1", "TAPE-2", chunk2, source_offset=100)
        plan = BackupPlan(
            backup_set_id="BS-1",
            source_root="/src",
            tapes=[tape1, tape2],
            source_files=[sf],
            segments=[seg1, seg2],
        )
        self.file_system.register(Path("/src/path/f1"), data)

        self.writer.write(plan)

        assert self.tape_drive.written["SEG-f1-001"] == chunk1

    # Test 2b — split file: correct second chunk written to tape 2
    def test_split_file_second_chunk_written_to_tape_two(self) -> None:
        data = b"A" * 100 + b"B" * 100
        tape1 = _make_tape("TAPE-1", seq=1)
        tape2 = _make_tape("TAPE-2", seq=2)
        sf = _make_file("f1", data)
        chunk1 = data[:100]
        chunk2 = data[100:]
        seg1 = _make_segment("SEG-f1-001", "f1", "TAPE-1", chunk1, source_offset=0)
        seg2 = _make_segment("SEG-f1-002", "f1", "TAPE-2", chunk2, source_offset=100)
        plan = BackupPlan(
            backup_set_id="BS-1",
            source_root="/src",
            tapes=[tape1, tape2],
            source_files=[sf],
            segments=[seg1, seg2],
        )
        self.file_system.register(Path("/src/path/f1"), data)

        self.writer.write(plan)

        assert self.tape_drive.written["SEG-f1-002"] == chunk2

    # Test 3 — file changed since scanning raises SourceFileChangedError
    def test_sha256_mismatch_raises_source_file_changed_error(self) -> None:
        original_data = b"original data"
        modified_data = b"modified data"  # different bytes → different sha256
        tape = _make_tape("TAPE-1")
        # source_file records the sha256 of original_data.
        sf = _make_file("f1", original_data)
        segment = _make_segment("SEG-f1-001", "f1", "TAPE-1", original_data)
        plan = BackupPlan(
            backup_set_id="BS-1",
            source_root="/src",
            tapes=[tape],
            source_files=[sf],
            segments=[segment],
        )
        # Register different bytes — simulates file modified after scanning.
        self.file_system.register(Path("/src/path/f1"), modified_data)

        with pytest.raises(SourceFileChangedError):
            self.writer.write(plan)

    # Test 4 — TapeFullError from tape_drive becomes FileWriteError
    def test_tape_full_raises_file_write_error(self) -> None:
        data = b"lots of data"
        tape = _make_tape("TAPE-1")
        sf = _make_file("f1", data)
        segment = _make_segment("SEG-f1-001", "f1", "TAPE-1", data)
        plan = BackupPlan(
            backup_set_id="BS-1",
            source_root="/src",
            tapes=[tape],
            source_files=[sf],
            segments=[segment],
        )
        self.file_system.register(Path("/src/path/f1"), data)
        self.tape_drive.raise_tape_full_on("SEG-f1-001")

        with pytest.raises(FileWriteError) as exc_info:
            self.writer.write(plan)

        assert isinstance(exc_info.value.__cause__, TapeFullError)

    # Test 5a — load/unload called in correct order for multi-tape plan
    def test_load_and_unload_called_for_each_tape_in_order(self) -> None:
        data1 = b"tape one data"
        data2 = b"tape two data"
        tape1 = _make_tape("TAPE-1", seq=1)
        tape2 = _make_tape("TAPE-2", seq=2)
        sf1 = _make_file("f1", data1)
        sf2 = _make_file("f2", data2)
        seg1 = _make_segment("SEG-f1-001", "f1", "TAPE-1", data1)
        seg2 = _make_segment("SEG-f2-001", "f2", "TAPE-2", data2)
        plan = BackupPlan(
            backup_set_id="BS-1",
            source_root="/src",
            tapes=[tape1, tape2],
            source_files=[sf1, sf2],
            segments=[seg1, seg2],
        )
        self.file_system.register(Path("/src/path/f1"), data1)
        self.file_system.register(Path("/src/path/f2"), data2)

        self.writer.write(plan)

        assert self.tape_drive.load_calls == ["TAPE-1", "TAPE-2"]
        assert self.tape_drive.unload_calls == 2

    # Test 5b — unload is called even when write raises (via finally)
    def test_unload_called_even_when_write_raises(self) -> None:
        original_data = b"some data"
        modified_data = b"tampered"
        tape = _make_tape("TAPE-1")
        sf = _make_file("f1", original_data)
        segment = _make_segment(
            "SEG-f1-001", "f1", "TAPE-1", original_data
        )
        plan = BackupPlan(
            backup_set_id="BS-1",
            source_root="/src",
            tapes=[tape],
            source_files=[sf],
            segments=[segment],
        )
        # Register modified bytes to trigger SourceFileChangedError.
        self.file_system.register(Path("/src/path/f1"), modified_data)

        with pytest.raises(SourceFileChangedError):
            self.writer.write(plan)

        assert self.tape_drive.unload_calls == 1

    # Test 6 — empty plan writes nothing and returns empty dict
    def test_empty_plan_writes_nothing(self) -> None:
        plan = BackupPlan(backup_set_id="BS-1", source_root="/src")

        result = self.writer.write(plan)

        assert result == {}
        assert self.tape_drive.load_calls == []
        assert self.tape_drive.unload_calls == 0
        assert self.tape_drive.written == {}

    # Test 7 — write returns dict mapping segment_id to slice sha256
    def test_write_returns_segment_sha256_map(self) -> None:
        data = b"A" * 100 + b"B" * 100
        tape1 = _make_tape("TAPE-1", seq=1)
        tape2 = _make_tape("TAPE-2", seq=2)
        sf = _make_file("f1", data)
        chunk1 = data[:100]
        chunk2 = data[100:]
        seg1 = _make_segment("SEG-f1-001", "f1", "TAPE-1", chunk1, source_offset=0)
        seg2 = _make_segment("SEG-f1-002", "f1", "TAPE-2", chunk2, source_offset=100)
        plan = BackupPlan(
            backup_set_id="BS-1",
            source_root="/src",
            tapes=[tape1, tape2],
            source_files=[sf],
            segments=[seg1, seg2],
        )
        self.file_system.register(Path("/src/path/f1"), data)

        result = self.writer.write(plan)

        assert result["SEG-f1-001"] == _sha256(chunk1)
        assert result["SEG-f1-002"] == _sha256(chunk2)
        # Slice sha256s differ from each other and from the full-file sha256.
        assert result["SEG-f1-001"] != result["SEG-f1-002"]
        assert result["SEG-f1-001"] != _sha256(data)

    # Test — TapeNotLoadedError wrapped into BackupPlanError
    def test_tape_not_loaded_raises_backup_plan_error(self) -> None:
        data = b"data"
        tape = _make_tape("TAPE-1")
        sf = _make_file("f1", data)
        segment = _make_segment("SEG-f1-001", "f1", "TAPE-1", data)
        plan = BackupPlan(
            backup_set_id="BS-1",
            source_root="/src",
            tapes=[tape],
            source_files=[sf],
            segments=[segment],
        )
        self.file_system.register(Path("/src/path/f1"), data)
        self.tape_drive.raise_not_loaded_on("TAPE-1")

        with pytest.raises(BackupPlanError) as exc_info:
            self.writer.write(plan)

        assert isinstance(exc_info.value.__cause__, TapeNotLoadedError)
