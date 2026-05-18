"""BackupWriter service — writes a BackupPlan to tape(s)."""

import hashlib
import logging
from collections import defaultdict
from pathlib import Path

from lto_backup.domain.backup_plan import BackupPlan
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
    """Writes every segment in a BackupPlan to the appropriate tape(s)."""

    def __init__(
        self,
        tape_drive: TapeDrive,
        file_system: FileSystem,
        file_hasher: FileHasher,
    ) -> None:
        self._tape_drive = tape_drive
        self._file_system = file_system
        self._file_hasher = file_hasher

    def write(self, plan: BackupPlan) -> dict[str, str]:
        """Execute the plan: load each tape, write its segments, then unload it.

        Returns a mapping of segment_id → sha256 of the bytes actually written
        (i.e. the hash of the slice, not the full file).
        """
        sha256_map: dict[str, str] = {}

        if not plan.tapes:
            logger.info("BackupWriter: plan is empty, nothing to write.")
            return sha256_map

        # Build a lookup: tape_id → list[TapeSegment] preserving plan order.
        segments_by_tape: dict[str, list[TapeSegment]] = defaultdict(list)
        for segment in plan.segments:
            segments_by_tape[segment.tape_id].append(segment)

        # Build lookups: file_id → path and file_id → SourceFile.
        path_by_file_id: dict[str, Path] = {
            sf.file_id: Path(sf.absolute_path) for sf in plan.source_files
        }
        source_file_by_id: dict[str, SourceFile] = {
            sf.file_id: sf for sf in plan.source_files
        }

        for tape in plan.tapes:
            tape_segments = segments_by_tape.get(tape.tape_id, [])
            logger.info(
                "BackupWriter: loading tape %s (%d segment(s) to write)",
                tape.tape_id,
                len(tape_segments),
            )
            try:
                self._tape_drive.load_tape(tape.tape_id)
            except TapeNotLoadedError as exc:
                raise BackupPlanError(
                    f"Tape {tape.tape_id!r} referenced in plan could not be loaded."
                ) from exc

            bytes_written = 0
            try:
                for segment in tape_segments:
                    file_path = path_by_file_id[segment.file_id]
                    file_data = self._file_system.open_for_read(file_path)

                    # Verify the full file has not changed since scanning.
                    actual_file_sha256 = self._file_hasher.hash_bytes(file_data)
                    source_file = source_file_by_id[segment.file_id]
                    if actual_file_sha256 != source_file.sha256:
                        raise SourceFileChangedError(
                            f"File {segment.file_id!r} has changed since planning: "
                            f"expected sha256 {source_file.sha256!r}, "
                            f"got {actual_file_sha256!r}."
                        )

                    chunk = file_data[
                        segment.source_offset : segment.source_offset + segment.length_bytes
                    ]
                    slice_sha256 = hashlib.sha256(chunk).hexdigest()
                    sha256_map[segment.segment_id] = slice_sha256

                    logger.debug(
                        "BackupWriter: writing segment %s (%d bytes)",
                        segment.segment_id,
                        len(chunk),
                    )
                    try:
                        self._tape_drive.write_bytes(segment.segment_id, chunk)
                    except TapeFullError as exc:
                        raise FileWriteError(
                            f"Tape full while writing segment {segment.segment_id!r}."
                        ) from exc

                    bytes_written += len(chunk)
            finally:
                self._tape_drive.unload_tape()

            logger.info(
                "BackupWriter: tape %s complete — %d byte(s) written.",
                tape.tape_id,
                bytes_written,
            )

        return sha256_map

