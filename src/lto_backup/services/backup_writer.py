"""BackupWriter service — writes a BackupPlan to tape(s) using container-based layout."""

import hashlib
import logging
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

_IO_CHUNK_SIZE = 4 << 20  # 4 MiB — maximum bytes per read_segment call

from lto_backup.domain.backup_plan import BackupPlan
from lto_backup.domain.container import Container
from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.exceptions.backup_plan_error import BackupPlanError
from lto_backup.exceptions.container_verification_error import ContainerVerificationError
from lto_backup.exceptions.file_write_error import FileWriteError
from lto_backup.exceptions.source_file_changed_error import SourceFileChangedError
from lto_backup.exceptions.tape_full_error import TapeFullError
from lto_backup.exceptions.tape_not_loaded_error import TapeNotLoadedError
from lto_backup.interfaces.file_hasher import FileHasher
from lto_backup.interfaces.file_system import FileSystem
from lto_backup.interfaces.tape_drive import TapeDrive

logger = logging.getLogger(__name__)


class BackupWriter:
    """Writes every container in a BackupPlan to the appropriate tape(s)."""

    def __init__(
        self,
        tape_drive: TapeDrive,
        file_system: FileSystem,
        file_hasher: FileHasher,
    ) -> None:
        self._tape_drive = tape_drive
        self._file_system = file_system
        self._file_hasher = file_hasher

    def compute_sha256s(
        self, plan: BackupPlan
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Read every source file and return ``(segment_map, container_map)``.

        ``segment_map``  maps ``segment_id``  → SHA-256 hex of that segment's bytes.
        ``container_map`` maps ``container_id`` → SHA-256 hex of the full container
        payload as it will be written to tape (segment bytes + zero-padding for any
        gaps and trailing space up to ``size_bytes``).

        Verifies the full-file SHA-256 against the scanned value and raises
        :class:`SourceFileChangedError` if any file has changed since planning.
        No tape drive operations are performed.
        """
        segment_map: dict[str, str] = {}
        container_map: dict[str, str] = {}
        verified_file_ids: set[str] = set()

        path_by_file_id: dict[str, Path] = {
            sf.file_id: Path(sf.absolute_path) for sf in plan.source_files
        }
        source_file_by_id: dict[str, SourceFile] = {
            sf.file_id: sf for sf in plan.source_files
        }

        segments_by_container: dict[str, list[TapeSegment]] = defaultdict(list)
        for segment in plan.segments:
            segments_by_container[segment.container_id].append(segment)
        for cid in segments_by_container:
            segments_by_container[cid].sort(key=lambda s: s.container_offset)

        for container in plan.containers:
            container_digest = hashlib.sha256()
            cursor = 0
            for segment in segments_by_container.get(container.container_id, []):
                file_id = segment.file_id
                file_path = path_by_file_id[file_id]

                if file_id not in verified_file_ids:
                    actual_sha256 = self._file_hasher.hash_file(file_path)
                    source_file = source_file_by_id[file_id]
                    if actual_sha256 != source_file.sha256:
                        raise SourceFileChangedError(
                            f"File {file_id!r} has changed since planning: "
                            f"expected sha256 {source_file.sha256!r}, "
                            f"got {actual_sha256!r}."
                        )
                    verified_file_ids.add(file_id)

                if segment.container_offset > cursor:
                    self._update_with_zero_padding(
                        container_digest, segment.container_offset - cursor
                    )
                    cursor = segment.container_offset

                seg_digest = hashlib.sha256()
                src_offset = segment.source_offset
                remaining = segment.length_bytes
                while remaining > 0:
                    n = min(remaining, _IO_CHUNK_SIZE)
                    piece = self._file_system.read_segment(file_path, src_offset, n)
                    seg_digest.update(piece)
                    container_digest.update(piece)
                    src_offset += len(piece)
                    remaining -= len(piece)
                segment_map[segment.segment_id] = seg_digest.hexdigest()
                cursor += segment.length_bytes

            if cursor < container.size_bytes:
                self._update_with_zero_padding(
                    container_digest, container.size_bytes - cursor
                )
            container_map[container.container_id] = container_digest.hexdigest()

        logger.info(
            "BackupWriter: pre-computed SHA-256s for %d segment(s) and %d container(s).",
            len(segment_map),
            len(container_map),
        )
        return segment_map, container_map

    @staticmethod
    def _update_with_zero_padding(digest: Any, length: int) -> None:
        remaining = length
        while remaining > 0:
            n = min(remaining, _IO_CHUNK_SIZE)
            digest.update(bytes(n))
            remaining -= n

    def write(
        self,
        plan: BackupPlan,
        post_tape_callback: Callable[[TapeDrive], None] | None = None,
    ) -> None:
        """Execute the plan: load each tape, write its containers, then unload it.

        If *post_tape_callback* is provided it is called with the tape drive
        after all containers for a tape have been written but before the tape
        is unloaded.  BackupService uses this to write the catalog to the tape
        while it is still loaded, avoiding a second load/unload cycle.
        """
        if not plan.tapes:
            logger.info("BackupWriter: plan is empty, nothing to write.")
            return

        # Build lookup: tape_id → list[Container] sorted by sequence_number.
        containers_by_tape: dict[str, list[Container]] = defaultdict(list)
        for container in plan.containers:
            containers_by_tape[container.tape_id].append(container)
        for tape_id in containers_by_tape:
            containers_by_tape[tape_id].sort(key=lambda c: c.sequence_number)

        # Build lookup: container_id → list[TapeSegment] sorted by container_offset.
        segments_by_container: dict[str, list[TapeSegment]] = defaultdict(list)
        for segment in plan.segments:
            segments_by_container[segment.container_id].append(segment)
        for cid in segments_by_container:
            segments_by_container[cid].sort(key=lambda s: s.container_offset)

        # Build lookups: file_id → path and file_id → SourceFile.
        path_by_file_id: dict[str, Path] = {
            sf.file_id: Path(sf.absolute_path) for sf in plan.source_files
        }
        source_file_by_id: dict[str, SourceFile] = {
            sf.file_id: sf for sf in plan.source_files
        }

        # Track verified files across all tapes so that files split across tape
        # boundaries are verified only once.
        verified_file_ids: set[str] = set()

        for tape in plan.tapes:
            tape_containers = containers_by_tape.get(tape.tape_id, [])
            logger.info(
                "BackupWriter: loading tape %s (%d container(s) to write)",
                tape.tape_id,
                len(tape_containers),
            )
            try:
                self._tape_drive.load_tape(tape.tape_id)
            except TapeNotLoadedError as exc:
                raise BackupPlanError(
                    f"Tape {tape.tape_id!r} referenced in plan could not be loaded."
                ) from exc

            bytes_written = 0
            try:
                for container in tape_containers:
                    segments = segments_by_container.get(container.container_id, [])

                    # Verify each file's full SHA-256 once before writing any of
                    # its segments to avoid partial writes on a mismatch.
                    for segment in segments:
                        file_id = segment.file_id
                        if file_id not in verified_file_ids:
                            file_path = path_by_file_id[file_id]
                            actual_sha256 = self._file_hasher.hash_file(file_path)
                            source_file = source_file_by_id[file_id]
                            if actual_sha256 != source_file.sha256:
                                raise SourceFileChangedError(
                                    f"File {file_id!r} has changed since planning: "
                                    f"expected sha256 {source_file.sha256!r}, "
                                    f"got {actual_sha256!r}."
                                )
                            verified_file_ids.add(file_id)

                    logger.debug(
                        "BackupWriter: writing container %s (%d bytes)",
                        container.container_id,
                        container.size_bytes,
                    )
                    write_digest = hashlib.sha256()
                    try:
                        self._tape_drive.write_stream(
                            container.container_id,
                            container.size_bytes,
                            self._hashing_chunks(
                                self._container_chunks(
                                    container, segments, path_by_file_id
                                ),
                                write_digest,
                            ),
                        )
                    except TapeFullError as exc:
                        raise FileWriteError(
                            f"Tape full while writing container {container.container_id!r}."
                        ) from exc

                    self._verify_container_readback(container, write_digest.hexdigest())
                    bytes_written += container.size_bytes

                if post_tape_callback is not None:
                    post_tape_callback(self._tape_drive)
            finally:
                self._tape_drive.unload_tape()

            logger.info(
                "BackupWriter: tape %s complete — %d byte(s) written.",
                tape.tape_id,
                bytes_written,
            )

    @staticmethod
    def _hashing_chunks(
        chunks: Iterator[bytes], digest: Any
    ) -> Iterator[bytes]:
        for chunk in chunks:
            digest.update(chunk)
            yield chunk

    def _verify_container_readback(
        self, container: Container, expected_sha256: str
    ) -> None:
        """Read the container back from the tape and verify its SHA-256."""
        readback_digest = hashlib.sha256()
        offset = 0
        remaining = container.size_bytes
        while remaining > 0:
            n = min(remaining, _IO_CHUNK_SIZE)
            piece = self._tape_drive.read_file_segment(
                container.container_id, offset, n
            )
            if not piece:
                break
            readback_digest.update(piece)
            offset += len(piece)
            remaining -= len(piece)
        actual = readback_digest.hexdigest()
        if actual != expected_sha256:
            raise ContainerVerificationError(
                f"Container {container.container_id!r} read-back SHA-256 mismatch: "
                f"expected {expected_sha256!r}, got {actual!r}."
            )
        logger.debug(
            "BackupWriter: container %s verified via read-back (sha256=%s)",
            container.container_id,
            expected_sha256,
        )

    def _container_chunks(
        self,
        container: Container,
        segments: list[TapeSegment],
        path_by_file_id: dict[str, Path],
    ) -> Iterator[bytes]:
        """Yield the bytes of a container in sequence, with zero-padding for gaps."""
        cursor = 0
        for segment in segments:  # segments must be sorted by container_offset
            if segment.container_offset > cursor:
                yield bytes(segment.container_offset - cursor)
            logger.debug(
                "BackupWriter: reading segment %s for container %s "
                "at offset %d (%d bytes)",
                segment.segment_id,
                container.container_id,
                segment.container_offset,
                segment.length_bytes,
            )
            src_offset = segment.source_offset
            remaining = segment.length_bytes
            while remaining > 0:
                n = min(remaining, _IO_CHUNK_SIZE)
                piece = self._file_system.read_segment(
                    path_by_file_id[segment.file_id], src_offset, n
                )
                yield piece
                src_offset += len(piece)
                remaining -= len(piece)
            cursor = segment.container_offset + segment.length_bytes
        if cursor < container.size_bytes:
            yield bytes(container.size_bytes - cursor)


