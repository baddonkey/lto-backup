"""BackupWriter service — writes a BackupPlan to tape(s) using container-based layout."""

import hashlib
import logging
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from lto_backup.domain.backup_plan import BackupPlan
from lto_backup.domain.container import Container
from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.exceptions.backup_plan_error import BackupPlanError
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

    def compute_sha256s(self, plan: BackupPlan) -> dict[str, str]:
        """Read every source file and return a mapping of segment_id → slice SHA-256.

        Verifies the full-file SHA-256 against the scanned value and raises
        SourceFileChangedError if any file has changed since planning.
        No tape drive operations are performed.
        """
        sha256_map: dict[str, str] = {}
        file_data_cache: dict[str, bytes] = {}
        verified_file_ids: set[str] = set()

        path_by_file_id: dict[str, Path] = {
            sf.file_id: Path(sf.absolute_path) for sf in plan.source_files
        }
        source_file_by_id: dict[str, SourceFile] = {
            sf.file_id: sf for sf in plan.source_files
        }

        for segment in plan.segments:
            file_id = segment.file_id
            file_path = path_by_file_id[file_id]

            if file_id not in file_data_cache:
                file_data_cache[file_id] = self._file_system.open_for_read(file_path)

            file_data = file_data_cache[file_id]

            if file_id not in verified_file_ids:
                actual_sha256 = self._file_hasher.hash_bytes(file_data)
                source_file = source_file_by_id[file_id]
                if actual_sha256 != source_file.sha256:
                    raise SourceFileChangedError(
                        f"File {file_id!r} has changed since planning: "
                        f"expected sha256 {source_file.sha256!r}, "
                        f"got {actual_sha256!r}."
                    )
                verified_file_ids.add(file_id)

            chunk = file_data[
                segment.source_offset : segment.source_offset + segment.length_bytes
            ]
            sha256_map[segment.segment_id] = hashlib.sha256(chunk).hexdigest()

        logger.info(
            "BackupWriter: pre-computed SHA-256s for %d segment(s).", len(sha256_map)
        )
        return sha256_map

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

        # File data and verification caches — shared across all tapes so that
        # files split across tape boundaries are read and verified only once.
        file_data_cache: dict[str, bytes] = {}
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
                    buffer = bytearray(container.size_bytes)
                    segments = segments_by_container.get(container.container_id, [])

                    for segment in segments:
                        file_id = segment.file_id
                        file_path = path_by_file_id[file_id]

                        # Load file data into cache on first access.
                        if file_id not in file_data_cache:
                            file_data_cache[file_id] = self._file_system.open_for_read(
                                file_path
                            )

                        file_data = file_data_cache[file_id]

                        # Verify full-file sha256 exactly once per file.
                        if file_id not in verified_file_ids:
                            actual_sha256 = self._file_hasher.hash_bytes(file_data)
                            source_file = source_file_by_id[file_id]
                            if actual_sha256 != source_file.sha256:
                                raise SourceFileChangedError(
                                    f"File {file_id!r} has changed since planning: "
                                    f"expected sha256 {source_file.sha256!r}, "
                                    f"got {actual_sha256!r}."
                                )
                            verified_file_ids.add(file_id)

                        chunk = file_data[
                            segment.source_offset : segment.source_offset
                            + segment.length_bytes
                        ]

                        buffer[
                            segment.container_offset : segment.container_offset
                            + segment.length_bytes
                        ] = chunk

                        logger.debug(
                            "BackupWriter: packed segment %s into container %s "
                            "at offset %d (%d bytes)",
                            segment.segment_id,
                            container.container_id,
                            segment.container_offset,
                            len(chunk),
                        )

                    logger.debug(
                        "BackupWriter: writing container %s (%d bytes)",
                        container.container_id,
                        container.size_bytes,
                    )
                    try:
                        self._tape_drive.write_bytes(container.container_id, bytes(buffer))
                    except TapeFullError as exc:
                        raise FileWriteError(
                            f"Tape full while writing container {container.container_id!r}."
                        ) from exc

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


