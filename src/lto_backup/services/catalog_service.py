"""CatalogService — builds and writes backup catalogs."""

import hashlib
import logging
from dataclasses import replace as _dc_replace

from lto_backup.domain.backup_plan import BackupPlan
from lto_backup.domain.catalog import Catalog
from lto_backup.exceptions.catalog_write_error import CatalogWriteError
from lto_backup.interfaces.catalog_serializer import CatalogSerializer
from lto_backup.interfaces.clock import Clock
from lto_backup.interfaces.tape_drive import TapeDrive

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "2.0"
_CATALOG_PATH = "catalog/catalog.json"
_CHECKSUM_PATH = "catalog/catalog.sha256"


class CatalogService:
    """Builds Catalog objects from a BackupPlan and writes them to tape."""

    def __init__(self, serializer: CatalogSerializer, clock: Clock) -> None:
        self._serializer = serializer
        self._clock = clock

    def build_catalog(self, plan: BackupPlan, segment_sha256s: dict[str, str]) -> Catalog:
        segments = [
            _dc_replace(seg, sha256=segment_sha256s[seg.segment_id])
            if seg.segment_id in segment_sha256s
            else seg
            for seg in plan.segments
        ]
        return Catalog(
            schema_version=_SCHEMA_VERSION,
            backup_set_id=plan.backup_set_id,
            created_at=self._clock.now(),
            source_root=plan.source_root,
            tapes=plan.tapes,
            containers=plan.containers,
            source_files=plan.source_files,
            segments=segments,
        )

    def write_catalog_to_tape(self, catalog: Catalog, tape_drive: TapeDrive) -> None:
        data = self._serializer.serialize(catalog)
        hex_digest = hashlib.sha256(data).hexdigest()
        try:
            tape_drive.write_bytes(_CATALOG_PATH, data)
        except Exception as exc:
            raise CatalogWriteError(
                f"Failed to write catalog to tape: {exc}"
            ) from exc
        logger.info(
            "Catalog written to tape: backup_set=%s size=%d bytes",
            catalog.backup_set_id,
            len(data),
        )
        try:
            tape_drive.write_bytes(_CHECKSUM_PATH, hex_digest.encode())
        except Exception as exc:
            raise CatalogWriteError(
                f"Failed to write catalog checksum to tape: {exc}"
            ) from exc
        logger.debug(
            "Catalog SHA-256 written to tape: backup_set=%s digest=%s",
            catalog.backup_set_id,
            hex_digest,
        )

