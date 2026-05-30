"""Unit tests for RestoreService."""

import hashlib
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from lto_backup.domain.catalog import Catalog
from lto_backup.domain.container import Container
from lto_backup.domain.restore_report import RestoreReport
from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape import Tape
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.exceptions.restore_error import RestoreError
from lto_backup.exceptions.tape_not_loaded_error import TapeNotLoadedError
from lto_backup.services.restore_service import RestoreService


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeTapeDrive:
    """In-memory tape drive keyed by tape_id → {filename → bytes}."""

    def __init__(
        self,
        tapes_data: dict[str, dict[str, bytes]],
        fail_load: set[str] | None = None,
    ) -> None:
        self._tapes_data = tapes_data
        self._fail_load: set[str] = fail_load or set()
        self._loaded: str | None = None
        self.load_calls: list[str] = []
        self.unload_calls: int = 0

    def load_tape(self, tape_id: str) -> None:
        self.load_calls.append(tape_id)
        if tape_id in self._fail_load:
            raise TapeNotLoadedError(f"Simulated load failure for {tape_id}")
        self._loaded = tape_id

    def unload_tape(self) -> None:
        self.unload_calls += 1
        self._loaded = None

    def current_tape_id(self) -> str:
        return self._loaded or ""

    def read_tape_id(self) -> str:
        return self._loaded or ""

    def remaining_capacity_bytes(self) -> int:
        return 0

    def write_file(self, source_path: Path, destination_name: str) -> None:
        raise NotImplementedError

    def write_bytes(self, destination_name: str, data: bytes) -> None:
        raise NotImplementedError

    def write_stream(
        self, destination_name: str, size_bytes: int, chunks: Iterator[bytes]
    ) -> None:
        raise NotImplementedError

    def read_file(self, name: str) -> bytes:
        assert self._loaded is not None
        return self._tapes_data[self._loaded][name]

    def read_file_segment(self, name: str, offset: int, length: int) -> bytes:
        assert self._loaded is not None
        data = self._tapes_data[self._loaded][name]
        return data[offset : offset + length]

    def list_files(self) -> list[str]:
        return []


class FakeFileSystem:
    """In-memory filesystem that records write_segment calls."""

    def __init__(self) -> None:
        self._buffers: dict[str, bytearray] = {}

    def list_files(self, root: Path) -> list[Path]:
        return []

    def file_size(self, path: Path) -> int:
        return len(self._buffers.get(str(path), b""))

    def modified_at_timestamp(self, path: Path) -> float:
        return 0.0

    def read_segment(self, path: Path, offset: int, length: int) -> bytes:
        buf = self._buffers.get(str(path), bytearray())
        return bytes(buf[offset : offset + length])

    def write_segment(self, path: Path, offset: int, data: bytes) -> None:
        key = str(path)
        if key not in self._buffers or offset == 0:
            if offset == 0:
                self._buffers[key] = bytearray()
        buf = self._buffers[key]
        end = offset + len(data)
        if len(buf) < end:
            buf.extend(b"\x00" * (end - len(buf)))
        buf[offset : end] = data

    def get_file_bytes(self, path: Path) -> bytes:
        return bytes(self._buffers.get(str(path), bytearray()))


class FakeFileHasher:
    """SHA-256 hasher backed by stdlib."""

    def hash_file(self, path: Path) -> str:
        # Delegate to FakeFileSystem isn't possible here — tests wire via hash_bytes.
        # This is only used for full-file verification; tests that exercise it
        # use a real Sha256FileHasher backed by tmp_path.
        raise NotImplementedError("Use Sha256FileHasher for full-file hash tests")

    def hash_bytes(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()


class FakeHasherWithFiles:
    """File hasher backed by a FakeFileSystem."""

    def __init__(self, fs: FakeFileSystem) -> None:
        self._fs = fs

    def hash_file(self, path: Path) -> str:
        data = self._fs.get_file_bytes(path)
        return hashlib.sha256(data).hexdigest()

    def hash_bytes(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()


class FakeCatalogSerializer:
    def __init__(self, catalog: Catalog | None = None) -> None:
        self._catalog = catalog

    def serialize(self, catalog: Catalog) -> bytes:
        return b"catalog"

    def deserialize(self, data: bytes) -> Catalog:
        assert self._catalog is not None
        return self._catalog


class FakeTapeSwitchService:
    """Calls load_tape directly without operator interaction."""

    def __init__(self, tape_drive: FakeTapeDrive) -> None:
        self._tape_drive = tape_drive

    def request_and_load(self, tape_id: str, sequence_number: int) -> None:
        self._tape_drive.load_tape(tape_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BACKUP_SET_ID = "BS-001"
_T1 = "TAPE-001"
_T2 = "TAPE-002"
_CREATED_AT = datetime(2026, 1, 1)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_tape(tape_id: str, seq: int = 1) -> Tape:
    return Tape(
        tape_id=tape_id,
        backup_set_id=_BACKUP_SET_ID,
        sequence_number=seq,
        nominal_capacity_bytes=100_000,
        reserved_catalog_bytes=100,
    )


def _make_container(
    container_id: str,
    tape_id: str,
    tape_offset: int = 0,
    size_bytes: int = 100,
) -> Container:
    return Container(
        container_id=container_id,
        backup_set_id=_BACKUP_SET_ID,
        tape_id=tape_id,
        sequence_number=1,
        tape_offset=tape_offset,
        size_bytes=size_bytes,
        sha256="",
    )


def _make_source_file(
    file_id: str,
    relative_path: str,
    data: bytes,
) -> SourceFile:
    return SourceFile(
        file_id=file_id,
        relative_path=relative_path,
        absolute_path=f"/src/{relative_path}",
        size_bytes=len(data),
        sha256=_sha256(data),
        modified_at=_CREATED_AT,
    )


def _make_segment(
    segment_id: str,
    file_id: str,
    container_id: str,
    data: bytes,
    container_offset: int = 0,
    source_offset: int = 0,
) -> TapeSegment:
    return TapeSegment(
        segment_id=segment_id,
        file_id=file_id,
        container_id=container_id,
        container_offset=container_offset,
        source_offset=source_offset,
        length_bytes=len(data),
        sha256=_sha256(data),
    )


def _build_service(
    tapes_data: dict[str, dict[str, bytes]],
    fail_load: set[str] | None = None,
    fs: FakeFileSystem | None = None,
) -> tuple[RestoreService, FakeTapeDrive, FakeFileSystem]:
    drive = FakeTapeDrive(tapes_data, fail_load)
    file_system = fs if fs is not None else FakeFileSystem()
    hasher = FakeHasherWithFiles(file_system)
    svc = RestoreService(
        tape_drive=drive,
        tape_switch_service=FakeTapeSwitchService(drive),
        serializer=FakeCatalogSerializer(),
        file_hasher=hasher,
        file_system=file_system,
    )
    return svc, drive, file_system


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRestoreSingleFileSingleSegment:
    """Single file, one segment, one tape — happy path."""

    def setup_method(self) -> None:
        self.file_data = b"hello restore"
        sf = _make_source_file("f1", "docs/note.txt", self.file_data)
        container = _make_container("CNT-001", _T1)
        seg = _make_segment("seg-001", "f1", "CNT-001", self.file_data)
        catalog = Catalog(
            schema_version="1.0",
            backup_set_id=_BACKUP_SET_ID,
            created_at=_CREATED_AT,
            source_root="/src",
            tapes=[_make_tape(_T1)],
            containers=[container],
            source_files=[sf],
            segments=[seg],
        )
        fs = FakeFileSystem()
        svc, self.drive, self.fs = _build_service(
            {_T1: {"CNT-001": self.file_data}}, fs=fs
        )
        self.report = svc.restore(catalog, restore_root=Path("/out"))
        self.dest = Path("/out") / "docs/note.txt"

    def test_no_errors(self) -> None:
        assert self.report.errors == []

    def test_files_requested(self) -> None:
        assert self.report.files_requested == 1

    def test_files_restored(self) -> None:
        assert self.report.files_restored == 1

    def test_content_written(self) -> None:
        assert self.fs.get_file_bytes(self.dest) == self.file_data

    def test_tape_loaded_and_unloaded(self) -> None:
        assert self.drive.load_calls == [_T1]
        assert self.drive.unload_calls == 1


class TestRestoreSingleFileTwoSegmentsOneContainer:
    """A file split across two segments within the same container."""

    def setup_method(self) -> None:
        part1 = b"AAAA"
        part2 = b"BBBB"
        full_data = part1 + part2
        container_bytes = full_data  # two segments packed end-to-end

        sf = _make_source_file("f1", "video.bin", full_data)
        container = _make_container("CNT-001", _T1, size_bytes=len(full_data))
        seg1 = _make_segment(
            "seg-001", "f1", "CNT-001", part1,
            container_offset=0, source_offset=0,
        )
        seg2 = _make_segment(
            "seg-002", "f1", "CNT-001", part2,
            container_offset=len(part1), source_offset=len(part1),
        )
        catalog = Catalog(
            schema_version="1.0",
            backup_set_id=_BACKUP_SET_ID,
            created_at=_CREATED_AT,
            source_root="/src",
            tapes=[_make_tape(_T1)],
            containers=[container],
            source_files=[sf],
            segments=[seg1, seg2],
        )
        fs = FakeFileSystem()
        svc, _, self.fs = _build_service({_T1: {"CNT-001": container_bytes}}, fs=fs)
        self.report = svc.restore(catalog, restore_root=Path("/out"))
        self.dest = Path("/out/video.bin")

    def test_no_errors(self) -> None:
        assert self.report.errors == []

    def test_file_reassembled_correctly(self) -> None:
        assert self.fs.get_file_bytes(self.dest) == b"AAAABBBB"


class TestRestoreSingleFileSpanningTwoTapes:
    """A file whose segments live on two different tapes."""

    def setup_method(self) -> None:
        part1 = b"tape1-data"
        part2 = b"tape2-data"
        full_data = part1 + part2

        sf = _make_source_file("f1", "big.bin", full_data)
        cnt1 = _make_container("CNT-001", _T1)
        cnt2 = _make_container("CNT-002", _T2)
        seg1 = _make_segment(
            "seg-001", "f1", "CNT-001", part1,
            container_offset=0, source_offset=0,
        )
        seg2 = _make_segment(
            "seg-002", "f1", "CNT-002", part2,
            container_offset=0, source_offset=len(part1),
        )
        catalog = Catalog(
            schema_version="1.0",
            backup_set_id=_BACKUP_SET_ID,
            created_at=_CREATED_AT,
            source_root="/src",
            tapes=[_make_tape(_T1, seq=1), _make_tape(_T2, seq=2)],
            containers=[cnt1, cnt2],
            source_files=[sf],
            segments=[seg1, seg2],
        )
        fs = FakeFileSystem()
        svc, self.drive, self.fs = _build_service(
            {
                _T1: {"CNT-001": part1},
                _T2: {"CNT-002": part2},
            },
            fs=fs,
        )
        self.report = svc.restore(catalog, restore_root=Path("/out"))

    def test_no_errors(self) -> None:
        assert self.report.errors == []

    def test_both_tapes_loaded(self) -> None:
        assert sorted(self.drive.load_calls) == [_T1, _T2]

    def test_file_reassembled(self) -> None:
        dest = Path("/out/big.bin")
        assert self.fs.get_file_bytes(dest) == b"tape1-datatape2-data"

    def test_tapes_loaded_in_sequence_order(self) -> None:
        assert self.drive.load_calls == [_T1, _T2]


class TestRestoreFilterGlob:
    """Only files matching the filter glob are restored."""

    def setup_method(self) -> None:
        data_txt = b"text content"
        data_bin = b"binary content"

        sf_txt = _make_source_file("f1", "docs/note.txt", data_txt)
        sf_bin = _make_source_file("f2", "media/video.bin", data_bin)
        cnt1 = _make_container("CNT-001", _T1)
        cnt2 = _make_container("CNT-002", _T1, tape_offset=100)
        seg_txt = _make_segment("seg-001", "f1", "CNT-001", data_txt)
        seg_bin = _make_segment("seg-002", "f2", "CNT-002", data_bin)

        catalog = Catalog(
            schema_version="1.0",
            backup_set_id=_BACKUP_SET_ID,
            created_at=_CREATED_AT,
            source_root="/src",
            tapes=[_make_tape(_T1)],
            containers=[cnt1, cnt2],
            source_files=[sf_txt, sf_bin],
            segments=[seg_txt, seg_bin],
        )
        fs = FakeFileSystem()
        svc, _, self.fs = _build_service(
            {_T1: {"CNT-001": data_txt, "CNT-002": data_bin}}, fs=fs
        )
        self.report = svc.restore(catalog, restore_root=Path("/out"), filter_glob="*.txt")

    def test_only_txt_requested(self) -> None:
        assert self.report.files_requested == 1

    def test_txt_restored(self) -> None:
        assert self.fs.get_file_bytes(Path("/out/docs/note.txt")) == b"text content"

    def test_bin_not_written(self) -> None:
        assert self.fs.get_file_bytes(Path("/out/media/video.bin")) == b""


class TestRestoreSegmentChecksumMismatch:
    """Segment SHA mismatch is a non-fatal error; other files still restored."""

    def setup_method(self) -> None:
        good_data = b"good file"
        corrupt_stored = b"XXXX"  # stored on tape but hash doesn't match

        sf_good = _make_source_file("f1", "good.txt", good_data)
        # Segment hash is for correct data, but tape stores corrupt bytes.
        corrupt_seg_hash = _sha256(good_data)
        sf_bad = _make_source_file("f2", "bad.txt", good_data)
        bad_segment = TapeSegment(
            segment_id="seg-bad",
            file_id="f2",
            container_id="CNT-002",
            container_offset=0,
            source_offset=0,
            length_bytes=len(corrupt_stored),
            sha256=corrupt_seg_hash,  # mismatch: hash of good_data, tape has corrupt_stored
        )

        cnt1 = _make_container("CNT-001", _T1)
        cnt2 = _make_container("CNT-002", _T1, tape_offset=100)
        seg_good = _make_segment("seg-001", "f1", "CNT-001", good_data)

        catalog = Catalog(
            schema_version="1.0",
            backup_set_id=_BACKUP_SET_ID,
            created_at=_CREATED_AT,
            source_root="/src",
            tapes=[_make_tape(_T1)],
            containers=[cnt1, cnt2],
            source_files=[sf_good, sf_bad],
            segments=[seg_good, bad_segment],
        )
        fs = FakeFileSystem()
        svc, _, self.fs = _build_service(
            {_T1: {"CNT-001": good_data, "CNT-002": corrupt_stored}}, fs=fs
        )
        self.report = svc.restore(catalog, restore_root=Path("/out"))

    def test_one_error_reported(self) -> None:
        assert len(self.report.errors) == 1

    def test_error_mentions_segment_id(self) -> None:
        assert "seg-bad" in self.report.errors[0]

    def test_good_file_still_restored(self) -> None:
        assert self.report.files_restored == 1

    def test_good_file_content_correct(self) -> None:
        assert self.fs.get_file_bytes(Path("/out/good.txt")) == b"good file"


class TestRestoreLoadCatalogFromTape:
    """load_catalog_from_tape deserializes catalog and unloads tape."""

    def setup_method(self) -> None:
        self.expected_catalog = Catalog(
            schema_version="1.0",
            backup_set_id="BS-TAPE",
            created_at=_CREATED_AT,
            source_root="/src",
        )
        catalog_bytes = b"serialized catalog"
        drive = FakeTapeDrive({_T1: {"catalog/catalog.json": catalog_bytes}})
        fs = FakeFileSystem()
        hasher = FakeHasherWithFiles(fs)
        serializer = FakeCatalogSerializer(catalog=self.expected_catalog)
        self.drive = drive
        self.svc = RestoreService(
            tape_drive=drive,
            tape_switch_service=FakeTapeSwitchService(drive),
            serializer=serializer,
            file_hasher=hasher,
            file_system=fs,
        )
        self.catalog = self.svc.load_catalog_from_tape(_T1)

    def test_returns_deserialized_catalog(self) -> None:
        assert self.catalog.backup_set_id == "BS-TAPE"

    def test_tape_unloaded_after_read(self) -> None:
        assert self.drive.unload_calls == 1

    def test_tape_loaded_before_read(self) -> None:
        assert _T1 in self.drive.load_calls


class TestRestoreLoadCatalogFromTapeLoadFailure:
    """load_catalog_from_tape raises RestoreError when load fails."""

    def test_raises_restore_error(self) -> None:
        drive = FakeTapeDrive({}, fail_load={_T1})
        fs = FakeFileSystem()
        hasher = FakeHasherWithFiles(fs)
        svc = RestoreService(
            tape_drive=drive,
            tape_switch_service=FakeTapeSwitchService(drive),
            serializer=FakeCatalogSerializer(),
            file_hasher=hasher,
            file_system=fs,
        )
        with pytest.raises(RestoreError):
            svc.load_catalog_from_tape(_T1)


class TestRestoreEmptyFilterMatch:
    """When the filter matches nothing, report 0 requested and 0 restored."""

    def test_empty_report_when_no_match(self) -> None:
        sf = _make_source_file("f1", "docs/note.txt", b"data")
        container = _make_container("CNT-001", _T1)
        seg = _make_segment("seg-001", "f1", "CNT-001", b"data")
        catalog = Catalog(
            schema_version="1.0",
            backup_set_id=_BACKUP_SET_ID,
            created_at=_CREATED_AT,
            source_root="/src",
            tapes=[_make_tape(_T1)],
            containers=[container],
            source_files=[sf],
            segments=[seg],
        )
        svc, drive, _ = _build_service({_T1: {"CNT-001": b"data"}})
        report = svc.restore(catalog, restore_root=Path("/out"), filter_glob="*.bin")

        assert report.files_requested == 0
        assert report.files_restored == 0
        assert report.errors == []
        assert drive.load_calls == []
