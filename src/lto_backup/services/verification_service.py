"""VerificationService — reads tape contents and verifies checksums."""

import hashlib
import logging
from collections import defaultdict

from lto_backup.domain.catalog import Catalog
from lto_backup.domain.container import Container
from lto_backup.domain.container_check import ContainerCheck
from lto_backup.domain.tape import Tape
from lto_backup.domain.tape_check import TapeCheck
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.domain.verification_report import VerificationReport
from lto_backup.interfaces.catalog_serializer import CatalogSerializer
from lto_backup.interfaces.file_hasher import FileHasher
from lto_backup.interfaces.tape_drive import TapeDrive

logger = logging.getLogger(__name__)

_CATALOG_PATH = "catalog/catalog.json"
_CHECKSUM_PATH = "catalog/catalog.sha256"
_IO_CHUNK_SIZE = 4 << 20  # 4 MiB — maximum bytes per read_file_segment call


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

    def verify(self, catalog: Catalog) -> VerificationReport:
        logger.info(
            "Verification started: backup_set=%s tapes=%d",
            catalog.backup_set_id,
            len(catalog.tapes),
        )
        tape_checks = [self._verify_tape(catalog, tape) for tape in catalog.tapes]
        report = VerificationReport(tape_checks=tape_checks)
        logger.info(
            "Verification complete: tapes_checked=%d errors=%d",
            len(catalog.tapes),
            len(report.errors),
        )
        return report

    def _verify_tape(self, catalog: Catalog, tape: Tape) -> TapeCheck:
        loaded = False
        try:
            self._tape_drive.load_tape(tape.tape_id)
            loaded = True
            catalog_passed, catalog_error = self._check_catalog_checksum(tape.tape_id)
            container_results = self._check_containers(catalog, tape.tape_id)
        except Exception as exc:
            msg = f"Tape {tape.tape_id}: operation failed: {exc}"
            logger.warning(msg)
            return TapeCheck(
                tape_id=tape.tape_id,
                sequence_number=tape.sequence_number,
                catalog_checksum_passed=False,
                catalog_error=msg,
                containers=[],
            )
        finally:
            if loaded:
                self._tape_drive.unload_tape()
        return TapeCheck(
            tape_id=tape.tape_id,
            sequence_number=tape.sequence_number,
            catalog_checksum_passed=catalog_passed,
            catalog_error=catalog_error,
            containers=container_results,
        )

    def _check_catalog_checksum(self, tape_id: str) -> tuple[bool, str | None]:
        stored_hex = self._tape_drive.read_file(_CHECKSUM_PATH).decode().strip()
        catalog_bytes = self._tape_drive.read_file(_CATALOG_PATH)
        actual_hex = self._file_hasher.hash_bytes(catalog_bytes)
        if actual_hex != stored_hex:
            msg = (
                f"Tape {tape_id}: catalog checksum mismatch "
                f"(expected {stored_hex}, got {actual_hex})"
            )
            logger.warning(msg)
            return False, msg
        return True, None

    def _check_containers(self, catalog: Catalog, tape_id: str) -> list[ContainerCheck]:
        results: list[ContainerCheck] = []

        # Build lookup: container_id → list[TapeSegment]
        segments_by_container: dict[str, list[TapeSegment]] = defaultdict(list)
        for seg in catalog.segments:
            segments_by_container[seg.container_id].append(seg)

        # Iterate containers on this tape in tape_offset order so the drive
        # streams forward rather than seeking back and forth.
        tape_containers = sorted(
            (c for c in catalog.containers if c.tape_id == tape_id),
            key=lambda c: c.tape_offset,
        )

        for container in tape_containers:
            container_errors: list[str] = []
            if container.sha256:
                container_hash_error = self._check_container_hash(tape_id, container)
                if container_hash_error is not None:
                    container_errors.append(container_hash_error)
                    results.append(ContainerCheck(
                        container_id=container.container_id,
                        passed=False,
                        errors=container_errors,
                    ))
                    # Skip segment-level checks for a container whose blob is already
                    # known to be corrupt — every segment in it would also mismatch.
                    continue

            for segment in segments_by_container.get(container.container_id, []):
                digest = hashlib.sha256()
                offset = segment.container_offset
                remaining = segment.length_bytes
                while remaining > 0:
                    n = min(remaining, _IO_CHUNK_SIZE)
                    piece = self._tape_drive.read_file_segment(
                        container.container_id, offset, n
                    )
                    if not piece:
                        break  # Unexpected EOF — checksum will mismatch below
                    digest.update(piece)
                    offset += len(piece)
                    remaining -= len(piece)
                actual_hash = digest.hexdigest()
                if actual_hash != segment.sha256:
                    msg = (
                        f"Tape {tape_id}: segment {segment.segment_id} "
                        f"checksum mismatch "
                        f"(expected {segment.sha256}, got {actual_hash})"
                    )
                    logger.warning(msg)
                    container_errors.append(msg)

            results.append(ContainerCheck(
                container_id=container.container_id,
                passed=not container_errors,
                errors=container_errors,
            ))
        return results

    def _check_container_hash(self, tape_id: str, container: Container) -> str | None:
        digest = hashlib.sha256()
        offset = 0
        remaining = container.size_bytes
        while remaining > 0:
            n = min(remaining, _IO_CHUNK_SIZE)
            piece = self._tape_drive.read_file_segment(
                container.container_id, offset, n
            )
            if not piece:
                break
            digest.update(piece)
            offset += len(piece)
            remaining -= len(piece)
        actual = digest.hexdigest()
        if actual != container.sha256:
            msg = (
                f"Tape {tape_id}: container {container.container_id} "
                f"checksum mismatch "
                f"(expected {container.sha256}, got {actual})"
            )
            logger.warning(msg)
            return msg
        return None

