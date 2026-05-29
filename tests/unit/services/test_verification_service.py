"""Unit tests for VerificationService."""

import hashlib
from datetime import datetime
from collections.abc import Iterator
from pathlib import Path

from lto_backup.domain.catalog import Catalog
from lto_backup.domain.container import Container
from lto_backup.domain.tape import Tape
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.services.verification_service import VerificationService


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
            raise RuntimeError(f"Simulated load failure for {tape_id}")
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
        pass

    def write_bytes(self, destination_name: str, data: bytes) -> None:
        pass

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


class FakeFileHasher:
    """SHA-256 hasher backed by stdlib."""

    def hash_file(self, path: Path) -> str:
        return ""

    def hash_bytes(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()


class FakeCatalogSerializer:
    def serialize(self, catalog: Catalog) -> bytes:
        return b"catalog"

    def deserialize(self, data: bytes) -> Catalog:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BACKUP_SET_ID = "BS-001"
_T1 = "TAPE-001"
_T2 = "TAPE-002"
_CREATED_AT = datetime(2026, 1, 1)


def _make_tape(tape_id: str) -> Tape:
    return Tape(
        tape_id=tape_id,
        backup_set_id=_BACKUP_SET_ID,
        sequence_number=1,
        nominal_capacity_bytes=100,
        reserved_catalog_bytes=10,
    )


def _make_container(
    container_id: str,
    tape_id: str,
    *,
    size_bytes: int = 100,
    sha256: str = "",
    tape_offset: int = 0,
) -> Container:
    return Container(
        container_id=container_id,
        backup_set_id=_BACKUP_SET_ID,
        tape_id=tape_id,
        sequence_number=1,
        tape_offset=tape_offset,
        size_bytes=size_bytes,
        sha256=sha256,
    )


def _make_segment(segment_id: str, container_id: str, data: bytes) -> TapeSegment:
    return TapeSegment(
        segment_id=segment_id,
        file_id="f1",
        container_id=container_id,
        container_offset=0,
        source_offset=0,
        length_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _tape_data(
    catalog_bytes: bytes,
    containers: list[tuple[str, bytes]],
    corrupt_catalog_sha256: bool = False,
) -> dict[str, bytes]:
    """Build the file dictionary for a single in-memory tape."""
    sha256_hex = hashlib.sha256(catalog_bytes).hexdigest()
    if corrupt_catalog_sha256:
        sha256_hex = "0" * 64
    result: dict[str, bytes] = {
        "catalog/catalog.json": catalog_bytes,
        "catalog/catalog.sha256": sha256_hex.encode(),
    }
    for container_id, container_bytes in containers:
        result[container_id] = container_bytes
    return result


def _make_service(
    tapes_data: dict[str, dict[str, bytes]],
    fail_load: set[str] | None = None,
) -> tuple[VerificationService, FakeTapeDrive]:
    drive = FakeTapeDrive(tapes_data, fail_load)
    svc = VerificationService(drive, FakeCatalogSerializer(), FakeFileHasher())
    return svc, drive


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVerifyAllMatch:
    def setup_method(self) -> None:
        seg_data = b"segment payload"
        container = _make_container("CNT-001", _T1)
        segment = _make_segment("seg-001", "CNT-001", seg_data)
        td = _tape_data(b"catalog json", [("CNT-001", seg_data)])
        svc, _ = _make_service({_T1: td})
        catalog = Catalog(
            schema_version="1.0",
            backup_set_id=_BACKUP_SET_ID,
            created_at=_CREATED_AT,
            source_root="/src",
            tapes=[_make_tape(_T1)],
            containers=[container],
            segments=[segment],
        )
        self.errors = svc.verify(catalog).errors

    def test_returns_empty_list(self) -> None:
        assert self.errors == []


class TestVerifySegmentChecksumMismatch:
    def setup_method(self) -> None:
        seg_data = b"segment payload"
        corrupt_data = b"corrupted"
        container = _make_container("CNT-001", _T1)
        segment = _make_segment("seg-002", "CNT-001", seg_data)
        # tape stores corrupt bytes for that container
        td = _tape_data(b"catalog json", [("CNT-001", corrupt_data)])
        svc, _ = _make_service({_T1: td})
        catalog = Catalog(
            schema_version="1.0",
            backup_set_id=_BACKUP_SET_ID,
            created_at=_CREATED_AT,
            source_root="/src",
            tapes=[_make_tape(_T1)],
            containers=[container],
            segments=[segment],
        )
        self.errors = svc.verify(catalog).errors

    def test_returns_one_error(self) -> None:
        assert len(self.errors) == 1

    def test_error_mentions_segment_id(self) -> None:
        assert "seg-002" in self.errors[0]


class TestVerifyCatalogChecksumMismatch:
    def setup_method(self) -> None:
        td = _tape_data(b"catalog json", [], corrupt_catalog_sha256=True)
        svc, _ = _make_service({_T1: td})
        catalog = Catalog(
            schema_version="1.0",
            backup_set_id=_BACKUP_SET_ID,
            created_at=_CREATED_AT,
            source_root="/src",
            tapes=[_make_tape(_T1)],
        )
        self.errors = svc.verify(catalog).errors

    def test_returns_one_error(self) -> None:
        assert len(self.errors) == 1

    def test_error_does_not_mention_segment(self) -> None:
        assert "segment" not in self.errors[0]


class TestVerifyTapeLoadFailure:
    def setup_method(self) -> None:
        td2 = _tape_data(b"catalog 2", [])
        svc, self.drive = _make_service({_T2: td2}, fail_load={_T1})
        catalog = Catalog(
            schema_version="1.0",
            backup_set_id=_BACKUP_SET_ID,
            created_at=_CREATED_AT,
            source_root="/src",
            tapes=[_make_tape(_T1), _make_tape(_T2)],
        )
        self.errors = svc.verify(catalog).errors

    def test_error_accumulated_for_failed_tape(self) -> None:
        assert any(_T1 in e for e in self.errors)

    def test_other_tape_still_checked(self) -> None:
        assert _T2 in self.drive.load_calls


class TestVerifyContainerChecksumMismatch:
    def setup_method(self) -> None:
        seg_data = b"segment payload"
        corrupt_data = b"X" * len(seg_data)
        good_hash = hashlib.sha256(seg_data).hexdigest()
        container = _make_container(
            "CNT-001", _T1, size_bytes=len(seg_data), sha256=good_hash
        )
        segment = _make_segment("seg-001", "CNT-001", seg_data)
        td = _tape_data(b"catalog json", [("CNT-001", corrupt_data)])
        svc, _ = _make_service({_T1: td})
        catalog = Catalog(
            schema_version="1.0",
            backup_set_id=_BACKUP_SET_ID,
            created_at=_CREATED_AT,
            source_root="/src",
            tapes=[_make_tape(_T1)],
            containers=[container],
            segments=[segment],
        )
        self.errors = svc.verify(catalog).errors

    def test_reports_container_error(self) -> None:
        assert any("container CNT-001" in e for e in self.errors)

    def test_skips_segment_error_when_container_already_bad(self) -> None:
        # Container-level failure should suppress per-segment errors for
        # the same blob (every segment would otherwise mismatch too).
        assert not any("seg-001" in e for e in self.errors)


class TestVerifyLoadUnloadCalledPerTape:
    def setup_method(self) -> None:
        td1 = _tape_data(b"catalog 1", [])
        td2 = _tape_data(b"catalog 2", [])
        svc, self.drive = _make_service({_T1: td1, _T2: td2})
        catalog = Catalog(
            schema_version="1.0",
            backup_set_id=_BACKUP_SET_ID,
            created_at=_CREATED_AT,
            source_root="/src",
            tapes=[_make_tape(_T1), _make_tape(_T2)],
        )
        svc.verify(catalog)

    def test_load_called_for_every_tape(self) -> None:
        assert sorted(self.drive.load_calls) == sorted([_T1, _T2])

    def test_unload_called_for_every_tape(self) -> None:
        assert self.drive.unload_calls == 2
