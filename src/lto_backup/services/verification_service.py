"""VerificationService — reads tape contents and verifies checksums."""

import logging

from lto_backup.domain.catalog import Catalog
from lto_backup.interfaces.catalog_serializer import CatalogSerializer
from lto_backup.interfaces.file_hasher import FileHasher
from lto_backup.interfaces.tape_drive import TapeDrive

logger = logging.getLogger(__name__)

_CATALOG_PATH = "catalog/catalog.json"
_CHECKSUM_PATH = "catalog/catalog.sha256"


class VerificationService:
    """Verifies catalog and segment checksums against tape contents."""

    def __init__(
        self,
        tape_drive: TapeDrive,
        serializer: CatalogSerializer,
        file_hasher: FileHasher,
    ) -> None:
        self._tape_drive = tape_drive
        self._serializer = serializer
        self._file_hasher = file_hasher

    def verify(self, catalog: Catalog) -> list[str]:
        logger.info(
            "Verification started: backup_set=%s tapes=%d",
            catalog.backup_set_id,
            len(catalog.tapes),
        )
        errors: list[str] = []
        for tape in catalog.tapes:
            errors.extend(self._verify_tape(catalog, tape.tape_id))
        logger.info(
            "Verification complete: tapes_checked=%d errors=%d",
            len(catalog.tapes),
            len(errors),
        )
        return errors

    def _verify_tape(self, catalog: Catalog, tape_id: str) -> list[str]:
        errors: list[str] = []
        loaded = False
        try:
            self._tape_drive.load_tape(tape_id)
            loaded = True
            errors.extend(self._check_catalog_checksum(tape_id))
            errors.extend(self._check_segments(catalog, tape_id))
        except Exception as exc:
            msg = f"Tape {tape_id}: operation failed: {exc}"
            logger.warning(msg)
            errors.append(msg)
        finally:
            if loaded:
                self._tape_drive.unload_tape()
        return errors

    def _check_catalog_checksum(self, tape_id: str) -> list[str]:
        stored_hex = self._tape_drive.read_file(_CHECKSUM_PATH).decode().strip()
        catalog_bytes = self._tape_drive.read_file(_CATALOG_PATH)
        actual_hex = self._file_hasher.hash_bytes(catalog_bytes)
        if actual_hex != stored_hex:
            msg = (
                f"Tape {tape_id}: catalog checksum mismatch "
                f"(expected {stored_hex}, got {actual_hex})"
            )
            logger.warning(msg)
            return [msg]
        return []

    def _check_segments(self, catalog: Catalog, tape_id: str) -> list[str]:
        errors: list[str] = []
        for segment in catalog.segments:
            if segment.tape_id != tape_id:
                continue
            data = self._tape_drive.read_file(segment.segment_id)
            actual_hash = self._file_hasher.hash_bytes(data)
            if actual_hash != segment.sha256:
                msg = (
                    f"Tape {tape_id}: segment {segment.segment_id} "
                    f"checksum mismatch "
                    f"(expected {segment.sha256}, got {actual_hash})"
                )
                logger.warning(msg)
                errors.append(msg)
        return errors

