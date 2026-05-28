"""Unit tests for CatalogService."""

import hashlib
from collections.abc import Iterator
from datetime import datetime

import pytest

from lto_backup.domain.backup_plan import BackupPlan
from lto_backup.domain.catalog import Catalog
from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape import Tape
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.exceptions.catalog_write_error import CatalogWriteError
from lto_backup.services.catalog_service import CatalogService


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, fixed_time: datetime) -> None:
        self._fixed_time = fixed_time

    def now(self) -> datetime:
        return self._fixed_time


class FakeCatalogSerializer:
    """Minimal serializer that round-trips a Catalog via JSON using the real serializer logic."""

    def __init__(self) -> None:
        from lto_backup.infrastructure.catalog.json_catalog_serializer import JsonCatalogSerializer

        self._real = JsonCatalogSerializer()
        self.serialize_calls: int = 0

    def serialize(self, catalog: Catalog) -> bytes:
        self.serialize_calls += 1
        return self._real.serialize(catalog)

    def deserialize(self, data: bytes) -> Catalog:
        return self._real.deserialize(data)


class FakeTapeDrive:
    """In-memory tape drive that records write_bytes calls."""

    def __init__(self) -> None:
        self.written: dict[str, bytes] = {}
        self._raise_on: set[str] = set()

    def raise_on_write(self, destination_name: str) -> None:
        self._raise_on.add(destination_name)

    def load_tape(self, tape_id: str) -> None: ...
    def unload_tape(self) -> None: ...
    def current_tape_id(self) -> str: return ""
    def remaining_capacity_bytes(self) -> int: return 2**40
    def write_file(self, source_path: object, destination_name: str) -> None: ...
    def read_file(self, name: str) -> bytes: return self.written[name]
    def list_files(self) -> list[str]: return list(self.written.keys())

    def write_bytes(self, destination_name: str, data: bytes) -> None:
        if destination_name in self._raise_on:
            raise OSError(f"Simulated write failure for {destination_name!r}")
        self.written[destination_name] = data

    def write_stream(
        self, destination_name: str, size_bytes: int, chunks: Iterator[bytes]
    ) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXED_TIME = datetime(2026, 5, 18, 12, 0, 0)


def _make_tape(tape_id: str = "TAPE-001") -> Tape:
    return Tape(
        tape_id=tape_id,
        backup_set_id="BS-1",
        sequence_number=1,
        nominal_capacity_bytes=100 * 2**30,
        reserved_catalog_bytes=1 * 2**20,
    )


def _make_source_file(file_id: str = "f1") -> SourceFile:
    return SourceFile(
        file_id=file_id,
        relative_path="records/file.txt",
        absolute_path="/source/records/file.txt",
        size_bytes=1024,
        sha256="abc123",
        modified_at=_FIXED_TIME,
    )


def _make_plan(
    *,
    backup_set_id: str = "BS-1",
    source_root: str = "/source",
) -> BackupPlan:
    tape = _make_tape()
    source_file = _make_source_file()
    segment = TapeSegment(
        segment_id="seg-1",
        file_id="f1",
        container_id="CNT-001",
        container_offset=0,
        source_offset=0,
        length_bytes=1024,
        sha256="abc123",
    )
    return BackupPlan(
        backup_set_id=backup_set_id,
        source_root=source_root,
        tapes=[tape],
        source_files=[source_file],
        segments=[segment],
    )


def _make_service() -> tuple[CatalogService, FakeClock, FakeCatalogSerializer]:
    clock = FakeClock(_FIXED_TIME)
    serializer = FakeCatalogSerializer()
    service = CatalogService(serializer=serializer, clock=clock)
    return service, clock, serializer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildCatalog:
    def test_build_catalog_copies_backup_set_id(self) -> None:
        service, _, _ = _make_service()
        plan = _make_plan(backup_set_id="BS-42")
        catalog = service.build_catalog(plan, {})
        assert catalog.backup_set_id == "BS-42"

    def test_build_catalog_copies_source_root(self) -> None:
        service, _, _ = _make_service()
        plan = _make_plan(source_root="/mnt/records")
        catalog = service.build_catalog(plan, {})
        assert catalog.source_root == "/mnt/records"

    def test_build_catalog_copies_tapes(self) -> None:
        service, _, _ = _make_service()
        plan = _make_plan()
        catalog = service.build_catalog(plan, {})
        assert catalog.tapes == plan.tapes

    def test_build_catalog_copies_source_files(self) -> None:
        service, _, _ = _make_service()
        plan = _make_plan()
        catalog = service.build_catalog(plan, {})
        assert catalog.source_files == plan.source_files

    def test_build_catalog_copies_segments_when_no_sha256s_provided(self) -> None:
        service, _, _ = _make_service()
        plan = _make_plan()
        catalog = service.build_catalog(plan, {})
        assert catalog.segments == plan.segments

    def test_build_catalog_fills_segment_sha256_from_dict(self) -> None:
        service, _, _ = _make_service()
        plan = _make_plan()
        sha256_map = {"seg-1": "deadbeef" * 8}
        catalog = service.build_catalog(plan, sha256_map)
        assert catalog.segments[0].sha256 == "deadbeef" * 8

    def test_build_catalog_segment_not_in_dict_keeps_original_sha256(self) -> None:
        service, _, _ = _make_service()
        plan = _make_plan()
        # Plan has segment "seg-1" with sha256="abc123"; dict is empty.
        catalog = service.build_catalog(plan, {})
        assert catalog.segments[0].sha256 == plan.segments[0].sha256

    def test_build_catalog_uses_clock_for_created_at(self) -> None:
        service, clock, _ = _make_service()
        plan = _make_plan()
        catalog = service.build_catalog(plan, {})
        assert catalog.created_at == _FIXED_TIME

    def test_build_catalog_sets_schema_version(self) -> None:
        service, _, _ = _make_service()
        plan = _make_plan()
        catalog = service.build_catalog(plan, {})
        assert catalog.schema_version == "2.0"


class TestWriteCatalogToTape:
    def _build_and_write(self) -> tuple[Catalog, FakeTapeDrive, FakeCatalogSerializer]:
        service, _, serializer = _make_service()
        tape_drive = FakeTapeDrive()
        plan = _make_plan()
        catalog = service.build_catalog(plan, {})
        service.write_catalog_to_tape(catalog, tape_drive)
        return catalog, tape_drive, serializer

    def test_write_catalog_to_tape_writes_catalog_json(self) -> None:
        _, tape_drive, _ = self._build_and_write()
        assert "catalog/catalog.json" in tape_drive.written

    def test_write_catalog_to_tape_writes_sha256_file(self) -> None:
        _, tape_drive, _ = self._build_and_write()
        assert "catalog/catalog.sha256" in tape_drive.written

    def test_write_catalog_to_tape_catalog_bytes_are_deserializable(self) -> None:
        catalog, tape_drive, serializer = self._build_and_write()
        data = tape_drive.written["catalog/catalog.json"]
        recovered = serializer.deserialize(data)
        assert recovered.backup_set_id == catalog.backup_set_id

    def test_write_catalog_to_tape_sha256_matches_catalog_bytes(self) -> None:
        _, tape_drive, _ = self._build_and_write()
        catalog_bytes = tape_drive.written["catalog/catalog.json"]
        expected_digest = hashlib.sha256(catalog_bytes).hexdigest()
        written_digest = tape_drive.written["catalog/catalog.sha256"].decode()
        assert written_digest == expected_digest

    def test_write_catalog_to_tape_raises_catalog_write_error_on_catalog_write_failure(
        self,
    ) -> None:
        service, _, _ = _make_service()
        tape_drive = FakeTapeDrive()
        tape_drive.raise_on_write("catalog/catalog.json")
        catalog = service.build_catalog(_make_plan(), {})
        with pytest.raises(CatalogWriteError):
            service.write_catalog_to_tape(catalog, tape_drive)

    def test_write_catalog_to_tape_raises_catalog_write_error_on_checksum_write_failure(
        self,
    ) -> None:
        service, _, _ = _make_service()
        tape_drive = FakeTapeDrive()
        tape_drive.raise_on_write("catalog/catalog.sha256")
        catalog = service.build_catalog(_make_plan(), {})
        with pytest.raises(CatalogWriteError):
            service.write_catalog_to_tape(catalog, tape_drive)

    def test_write_catalog_to_tape_chains_original_exception(self) -> None:
        service, _, _ = _make_service()
        tape_drive = FakeTapeDrive()
        tape_drive.raise_on_write("catalog/catalog.json")
        catalog = service.build_catalog(_make_plan(), {})
        with pytest.raises(CatalogWriteError) as exc_info:
            service.write_catalog_to_tape(catalog, tape_drive)
        assert exc_info.value.__cause__ is not None
