"""RestoreService — reads containers from tape and reassembles source files."""

import fnmatch
import hashlib
import logging
from collections import defaultdict
from pathlib import Path

from lto_backup.domain.catalog import Catalog
from lto_backup.domain.container import Container
from lto_backup.domain.container_restore_result import ContainerRestoreResult
from lto_backup.domain.restore_report import RestoreReport
from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.exceptions.restore_error import RestoreError
from lto_backup.interfaces.catalog_serializer import CatalogSerializer
from lto_backup.interfaces.file_hasher import FileHasher
from lto_backup.interfaces.file_system import FileSystem
from lto_backup.interfaces.tape_drive import TapeDrive
from lto_backup.services.tape_switch_service import TapeSwitchService

logger = logging.getLogger(__name__)

_CATALOG_PATH = "catalog/catalog.json"
_IO_CHUNK_SIZE = 4 << 20  # 4 MiB — matches VerificationService chunk size


class RestoreService:
    """Reassembles source files from tape containers using the backup catalog."""

    def __init__(
        self,
        tape_drive: TapeDrive,
        tape_switch_service: TapeSwitchService,
        serializer: CatalogSerializer,
        file_hasher: FileHasher,
        file_system: FileSystem,
    ) -> None:
        self._tape_drive = tape_drive
        self._tape_switch_service = tape_switch_service
        self._serializer = serializer
        self._file_hasher = file_hasher
        self._file_system = file_system

    def load_catalog_from_tape(self, tape_id: str) -> Catalog:
        """Load and deserialize the catalog from the named tape.

        The tape is loaded, the catalog is read, and the tape is unloaded in
        a finally block.  Raises RestoreError if any step fails.
        """
        loaded = False
        try:
            logger.info("Loading tape %s to read catalog.", tape_id)
            self._tape_drive.load_tape(tape_id)
            loaded = True
            catalog_bytes = self._tape_drive.read_file(_CATALOG_PATH)
            catalog = self._serializer.deserialize(catalog_bytes)
            logger.info(
                "Catalog loaded from tape %s: backup_set=%s files=%d",
                tape_id,
                catalog.backup_set_id,
                len(catalog.source_files),
            )
            return catalog
        except RestoreError:
            raise
        except Exception as exc:
            logger.error("Failed to read catalog from tape %s: %s", tape_id, exc)
            raise RestoreError(
                f"Cannot read catalog from tape {tape_id}: {exc}"
            ) from exc
        finally:
            if loaded:
                try:
                    self._tape_drive.unload_tape()
                except Exception as unload_exc:
                    logger.warning(
                        "Failed to unload tape %s after catalog read: %s",
                        tape_id,
                        unload_exc,
                    )

    def restore(
        self,
        catalog: Catalog,
        restore_root: Path,
        filter_glob: str | None = None,
    ) -> RestoreReport:
        """Restore files from tape to restore_root.

        If filter_glob is given, only files whose relative_path matches the
        fnmatch pattern are restored.
        """
        logger.info(
            "Restore started: backup_set=%s restore_root=%s filter=%s",
            catalog.backup_set_id,
            restore_root,
            filter_glob,
        )

        # Select the files we want to restore.
        target_files: list[SourceFile] = [
            sf
            for sf in catalog.source_files
            if filter_glob is None
            or fnmatch.fnmatch(sf.relative_path, filter_glob)
        ]
        files_requested = len(target_files)
        logger.info("Files selected for restore: %d", files_requested)

        if not target_files:
            return RestoreReport(
                files_requested=0, files_restored=0, errors=[]
            )

        # Build fast-lookup indexes.
        target_file_ids: set[str] = {sf.file_id for sf in target_files}
        file_by_id: dict[str, SourceFile] = {sf.file_id: sf for sf in target_files}
        container_by_id: dict[str, Container] = {
            c.container_id: c for c in catalog.containers
        }

        # Segments that belong to target files, grouped by container.
        segments_by_container: dict[str, list[TapeSegment]] = defaultdict(list)
        for seg in catalog.segments:
            if seg.file_id in target_file_ids:
                segments_by_container[seg.container_id].append(seg)

        # Collect all errors and track which files have bad segments.
        all_errors: list[str] = []
        files_with_segment_errors: set[str] = set()
        failed_paths: list[str] = []
        hash_failures: list[str] = []
        container_results: list[ContainerRestoreResult] = []

        # Process one tape at a time, in sequence_number order.
        tapes_sorted = sorted(catalog.tapes, key=lambda t: t.sequence_number)

        for tape in tapes_sorted:
            # Which containers on this tape hold target segments?
            tape_containers = sorted(
                (
                    container_by_id[seg.container_id]
                    for seg in catalog.segments
                    if seg.file_id in target_file_ids
                    and container_by_id[seg.container_id].tape_id == tape.tape_id
                ),
                key=lambda c: c.tape_offset,
            )
            # Deduplicate (multiple segments can share a container).
            seen: set[str] = set()
            unique_tape_containers: list[Container] = []
            for c in tape_containers:
                if c.container_id not in seen:
                    seen.add(c.container_id)
                    unique_tape_containers.append(c)

            if not unique_tape_containers:
                logger.debug(
                    "Tape %s has no containers relevant to the restore; skipping.",
                    tape.tape_id,
                )
                continue

            logger.info(
                "Requesting tape %s (sequence %d) — %d container(s) to read.",
                tape.tape_id,
                tape.sequence_number,
                len(unique_tape_containers),
            )
            self._tape_switch_service.request_and_load(
                tape.tape_id, tape.sequence_number
            )
            try:
                self._restore_from_tape(
                    tape.tape_id,
                    unique_tape_containers,
                    segments_by_container,
                    file_by_id,
                    restore_root,
                    all_errors,
                    files_with_segment_errors,
                    container_results,
                )
            finally:
                self._tape_drive.unload_tape()

        # After all tapes: verify full-file SHA-256 for each successfully-restored file.
        # Zero-byte files have no segments and are never written by the segment loop,
        # so create them explicitly here before the hash check.
        for sf in target_files:
            if sf.size_bytes == 0 and sf.file_id not in files_with_segment_errors:
                dest = restore_root / sf.relative_path
                if not dest.exists():
                    logger.debug("Creating empty file: %s", dest)
                    try:
                        self._file_system.write_segment(dest, 0, b"")
                    except Exception as exc:
                        msg = f"File {sf.relative_path}: cannot create empty file: {exc}"
                        logger.warning(msg)
                        files_with_segment_errors.add(sf.file_id)
                        all_errors.append(msg)

        files_restored = 0
        for sf in target_files:
            if sf.file_id in files_with_segment_errors:
                # Segment-level errors already recorded; skip full-file check.
                failed_paths.append(sf.relative_path)
                continue
            dest = restore_root / sf.relative_path
            try:
                actual_hash = self._file_hasher.hash_file(dest)
            except Exception as exc:
                msg = f"File {sf.relative_path}: cannot hash restored file: {exc}"
                logger.warning(msg)
                all_errors.append(msg)
                failed_paths.append(sf.relative_path)
                continue
            if actual_hash != sf.sha256:
                msg = (
                    f"File {sf.relative_path}: full-file SHA-256 mismatch "
                    f"(expected {sf.sha256}, got {actual_hash})"
                )
                logger.warning(msg)
                all_errors.append(msg)
                failed_paths.append(sf.relative_path)
                hash_failures.append(sf.relative_path)
                continue
            files_restored += 1
            try:
                self._file_system.set_attributes(
                    dest, sf.modified_at.timestamp(), sf.unix_mode
                )
            except OSError as exc:
                logger.warning("Cannot set attributes on %s: %s", dest, exc)

        logger.info(
            "Restore complete: requested=%d restored=%d errors=%d",
            files_requested,
            files_restored,
            len(all_errors),
        )
        return RestoreReport(
            files_requested=files_requested,
            files_restored=files_restored,
            errors=all_errors,
            failed_paths=failed_paths,
            container_results=container_results,
            hash_failures=hash_failures,
        )

    def _restore_from_tape(
        self,
        tape_id: str,
        containers: list[Container],
        segments_by_container: dict[str, list[TapeSegment]],
        file_by_id: dict[str, SourceFile],
        restore_root: Path,
        all_errors: list[str],
        files_with_segment_errors: set[str],
        container_results: list[ContainerRestoreResult],
    ) -> None:
        for container in containers:
            # Verify container-level SHA-256 if the catalog recorded one.
            hash_error = self._verify_container_hash(tape_id, container)
            container_results.append(ContainerRestoreResult(
                container_id=container.container_id,
                tape_id=tape_id,
                sha256_passed=hash_error is None,
                error=hash_error,
            ))
            if hash_error is not None:
                all_errors.append(hash_error)
                # Mark every file in this container as failed.
                for seg in segments_by_container.get(container.container_id, []):
                    files_with_segment_errors.add(seg.file_id)
                continue

            segs = sorted(
                segments_by_container.get(container.container_id, []),
                key=lambda s: s.container_offset,
            )
            for seg in segs:
                sf = file_by_id[seg.file_id]
                dest = restore_root / sf.relative_path
                error = self._restore_segment(tape_id, container, seg, dest)
                if error is not None:
                    all_errors.append(error)
                    files_with_segment_errors.add(seg.file_id)

    def _verify_container_hash(
        self,
        tape_id: str,
        container: Container,
    ) -> str | None:
        """Hash the full container blob and compare against catalog SHA-256.

        Returns an error string on mismatch, or None when the hash matches
        (or when the catalog has no hash recorded for this container).
        """
        if not container.sha256:
            return None
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
                f"SHA-256 mismatch "
                f"(expected {container.sha256}, got {actual})"
            )
            logger.warning(msg)
            return msg
        return None

    def _restore_segment(
        self,
        tape_id: str,
        container: Container,
        seg: TapeSegment,
        dest: Path,
    ) -> str | None:
        """Read one segment from the tape and write it to dest.

        Returns an error string on failure, or None on success.
        """
        digest = hashlib.sha256()
        tape_read_offset = seg.container_offset
        file_write_offset = seg.source_offset
        remaining = seg.length_bytes

        while remaining > 0:
            n = min(remaining, _IO_CHUNK_SIZE)
            piece = self._tape_drive.read_file_segment(
                container.container_id, tape_read_offset, n
            )
            if not piece:
                msg = (
                    f"Tape {tape_id}: segment {seg.segment_id} "
                    f"unexpected EOF at offset {tape_read_offset}"
                )
                logger.warning(msg)
                return msg
            digest.update(piece)
            self._file_system.write_segment(dest, file_write_offset, piece)
            tape_read_offset += len(piece)
            file_write_offset += len(piece)
            remaining -= len(piece)

        actual_hash = digest.hexdigest()
        if actual_hash != seg.sha256:
            msg = (
                f"Tape {tape_id}: segment {seg.segment_id} "
                f"checksum mismatch "
                f"(expected {seg.sha256}, got {actual_hash})"
            )
            logger.warning(msg)
            return msg

        logger.debug(
            "Segment %s restored to %s (offset=%d length=%d)",
            seg.segment_id,
            dest,
            seg.source_offset,
            seg.length_bytes,
        )
        return None
