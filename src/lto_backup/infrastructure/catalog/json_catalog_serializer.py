import json
import logging
from datetime import datetime
from typing import Any

from lto_backup.domain.catalog import Catalog
from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape import Tape
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.exceptions.catalog_write_error import CatalogWriteError

_SCHEMA_VERSION = "1.0"

logger = logging.getLogger(__name__)


class JsonCatalogSerializer:
    """Serializes and deserializes a Catalog to/from UTF-8 JSON bytes."""

    def serialize(self, catalog: Catalog) -> bytes:
        logger.debug(
            "Serializing catalog for backup set %s (%d files, %d tapes)",
            catalog.backup_set_id,
            len(catalog.source_files),
            len(catalog.tapes),
        )
        try:
            payload = {
                "schema_version": catalog.schema_version,
                "backup_set_id": catalog.backup_set_id,
                "created_at": catalog.created_at.isoformat(),
                "source_root": catalog.source_root,
                "tapes": [self._tape_to_dict(t) for t in catalog.tapes],
                "source_files": [self._source_file_to_dict(f) for f in catalog.source_files],
                "segments": [self._segment_to_dict(s) for s in catalog.segments],
            }
            data = json.dumps(payload, indent=2).encode("utf-8")
            logger.info(
                "Catalog serialized: backup_set=%s size=%d bytes",
                catalog.backup_set_id,
                len(data),
            )
            return data
        except Exception as exc:
            logger.error("Failed to serialize catalog for %s: %s", catalog.backup_set_id, exc)
            raise CatalogWriteError(f"Failed to serialize catalog: {exc}") from exc

    def deserialize(self, data: bytes) -> Catalog:
        logger.debug("Deserializing catalog (%d bytes)", len(data))
        try:
            payload = json.loads(data.decode("utf-8"))
            catalog = Catalog(
                schema_version=payload["schema_version"],
                backup_set_id=payload["backup_set_id"],
                created_at=datetime.fromisoformat(payload["created_at"]),
                source_root=payload["source_root"],
                tapes=[self._tape_from_dict(t) for t in payload.get("tapes", [])],
                source_files=[
                    self._source_file_from_dict(f) for f in payload.get("source_files", [])
                ],
                segments=[self._segment_from_dict(s) for s in payload.get("segments", [])],
            )
            logger.info(
                "Catalog deserialized: backup_set=%s files=%d tapes=%d",
                catalog.backup_set_id,
                len(catalog.source_files),
                len(catalog.tapes),
            )
            return catalog
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            logger.error("Failed to deserialize catalog: %s", exc)
            raise CatalogWriteError(f"Failed to deserialize catalog: {exc}") from exc

    # ------------------------------------------------------------------
    # Private helpers — serialize
    # ------------------------------------------------------------------

    @staticmethod
    def _tape_to_dict(tape: Tape) -> dict[str, Any]:
        return {
            "tape_id": tape.tape_id,
            "backup_set_id": tape.backup_set_id,
            "sequence_number": tape.sequence_number,
            "nominal_capacity_bytes": tape.nominal_capacity_bytes,
            "reserved_catalog_bytes": tape.reserved_catalog_bytes,
        }

    @staticmethod
    def _source_file_to_dict(sf: SourceFile) -> dict[str, Any]:
        return {
            "file_id": sf.file_id,
            "relative_path": sf.relative_path,
            "absolute_path": sf.absolute_path,
            "size_bytes": sf.size_bytes,
            "sha256": sf.sha256,
            "modified_at": sf.modified_at.isoformat(),
        }

    @staticmethod
    def _segment_to_dict(s: TapeSegment) -> dict[str, Any]:
        return {
            "segment_id": s.segment_id,
            "file_id": s.file_id,
            "tape_id": s.tape_id,
            "tape_offset": s.tape_offset,
            "source_offset": s.source_offset,
            "length_bytes": s.length_bytes,
            "sha256": s.sha256,
        }

    # ------------------------------------------------------------------
    # Private helpers — deserialize
    # ------------------------------------------------------------------

    @staticmethod
    def _tape_from_dict(d: dict[str, Any]) -> Tape:
        return Tape(
            tape_id=str(d["tape_id"]),
            backup_set_id=str(d["backup_set_id"]),
            sequence_number=int(d["sequence_number"]),
            nominal_capacity_bytes=int(d["nominal_capacity_bytes"]),
            reserved_catalog_bytes=int(d["reserved_catalog_bytes"]),
        )

    @staticmethod
    def _source_file_from_dict(d: dict[str, Any]) -> SourceFile:
        return SourceFile(
            file_id=str(d["file_id"]),
            relative_path=str(d["relative_path"]),
            absolute_path=str(d["absolute_path"]),
            size_bytes=int(d["size_bytes"]),
            sha256=str(d["sha256"]),
            modified_at=datetime.fromisoformat(str(d["modified_at"])),
        )

    @staticmethod
    def _segment_from_dict(d: dict[str, Any]) -> TapeSegment:
        return TapeSegment(
            segment_id=str(d["segment_id"]),
            file_id=str(d["file_id"]),
            tape_id=str(d["tape_id"]),
            tape_offset=int(d["tape_offset"]),
            source_offset=int(d["source_offset"]),
            length_bytes=int(d["length_bytes"]),
            sha256=str(d["sha256"]),
        )

