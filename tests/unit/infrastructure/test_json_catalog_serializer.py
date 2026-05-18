from datetime import UTC, datetime

import pytest

from lto_backup.domain.catalog import Catalog
from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape import Tape
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.exceptions.catalog_write_error import CatalogWriteError
from lto_backup.infrastructure.catalog.json_catalog_serializer import JsonCatalogSerializer


def _make_catalog() -> Catalog:
    tape = Tape(
        tape_id="TAPE-001",
        backup_set_id="BSET-001",
        sequence_number=1,
        nominal_capacity_bytes=12_000_000_000,
        reserved_catalog_bytes=100_000_000,
    )
    source_file = SourceFile(
        file_id="FILE-001",
        relative_path="records/case-001/video.bin",
        absolute_path="/data/records/case-001/video.bin",
        size_bytes=1_073_741_824,
        sha256="abc123",
        modified_at=datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC),
    )
    segment = TapeSegment(
        segment_id="SEG-001",
        file_id="FILE-001",
        tape_id="TAPE-001",
        tape_offset=0,
        source_offset=0,
        length_bytes=1_073_741_824,
        sha256="ghi789",
    )
    return Catalog(
        schema_version="1.0",
        backup_set_id="BSET-001",
        created_at=datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC),
        source_root="/data/records",
        tapes=[tape],
        source_files=[source_file],
        segments=[segment],
    )


class TestJsonCatalogSerializer:
    def setup_method(self) -> None:
        self.serializer = JsonCatalogSerializer()

    def test_serialize_returns_bytes(self) -> None:
        catalog = _make_catalog()
        result = self.serializer.serialize(catalog)
        assert isinstance(result, bytes)

    def test_serialize_is_valid_json(self) -> None:
        import json

        catalog = _make_catalog()
        data = self.serializer.serialize(catalog)
        parsed = json.loads(data)
        assert parsed["backup_set_id"] == "BSET-001"

    def test_round_trip(self) -> None:
        catalog = _make_catalog()
        data = self.serializer.serialize(catalog)
        restored = self.serializer.deserialize(data)
        assert restored == catalog

    def test_round_trip_preserves_tape(self) -> None:
        catalog = _make_catalog()
        data = self.serializer.serialize(catalog)
        restored = self.serializer.deserialize(data)
        assert len(restored.tapes) == 1
        assert restored.tapes[0].tape_id == "TAPE-001"
        assert restored.tapes[0].usable_capacity_bytes() == catalog.tapes[0].usable_capacity_bytes()

    def test_round_trip_preserves_source_file(self) -> None:
        catalog = _make_catalog()
        data = self.serializer.serialize(catalog)
        restored = self.serializer.deserialize(data)
        assert len(restored.source_files) == 1
        assert restored.source_files[0].file_id == "FILE-001"

    def test_round_trip_preserves_segment(self) -> None:
        catalog = _make_catalog()
        data = self.serializer.serialize(catalog)
        restored = self.serializer.deserialize(data)
        assert len(restored.segments) == 1
        assert restored.segments[0].source_offset == 0
        assert restored.segments[0].tape_offset == 0

    def test_datetime_round_trip_utc(self) -> None:
        catalog = _make_catalog()
        data = self.serializer.serialize(catalog)
        restored = self.serializer.deserialize(data)
        assert restored.created_at == catalog.created_at

    def test_datetime_round_trip_source_file(self) -> None:
        catalog = _make_catalog()
        data = self.serializer.serialize(catalog)
        restored = self.serializer.deserialize(data)
        assert restored.source_files[0].modified_at == catalog.source_files[0].modified_at

    def test_deserialize_raises_on_invalid_json(self) -> None:
        with pytest.raises(CatalogWriteError):
            self.serializer.deserialize(b"not valid json")

    def test_deserialize_raises_on_missing_field(self) -> None:
        import json

        data = json.dumps({"schema_version": "1.0"}).encode()
        with pytest.raises(CatalogWriteError):
            self.serializer.deserialize(data)

    def test_empty_catalog_round_trip(self) -> None:
        catalog = Catalog(
            schema_version="1.0",
            backup_set_id="BSET-EMPTY",
            created_at=datetime(2026, 5, 18, 0, 0, 0, tzinfo=UTC),
            source_root="/empty",
        )
        data = self.serializer.serialize(catalog)
        restored = self.serializer.deserialize(data)
        assert restored == catalog
