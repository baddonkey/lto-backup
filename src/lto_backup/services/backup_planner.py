"""BackupPlanner service — allocates source files to tapes and produces a BackupPlan."""

import logging
import uuid

from lto_backup.config.backup_config import BackupConfig
from lto_backup.domain.backup_plan import BackupPlan
from lto_backup.domain.catalog import Catalog
from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape import Tape
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.exceptions.backup_plan_error import BackupPlanError
from lto_backup.interfaces.catalog_serializer import CatalogSerializer
from lto_backup.interfaces.clock import Clock

_CATALOG_SCHEMA_VERSION = "1.0"

logger = logging.getLogger(__name__)


class BackupPlanner:
    """Allocates source files to tapes using a two-pass algorithm and returns a BackupPlan."""

    def __init__(self, serializer: CatalogSerializer, clock: Clock) -> None:
        self._serializer = serializer
        self._clock = clock

    def plan(self, source_files: list[SourceFile], config: BackupConfig) -> BackupPlan:
        """Create a BackupPlan allocating *source_files* across tapes per *config*."""
        if config.tape_nominal_capacity_bytes <= 0:
            raise BackupPlanError(
                f"tape_nominal_capacity_bytes must be > 0, "
                f"got {config.tape_nominal_capacity_bytes}"
            )

        backup_set_id = str(uuid.uuid4())

        # Pass 1 — measure serialized draft catalog to determine reserved bytes per tape.
        draft_catalog = Catalog(
            schema_version=_CATALOG_SCHEMA_VERSION,
            backup_set_id=backup_set_id,
            created_at=self._clock.now(),
            source_root=str(config.source_root),
            tapes=[],
            source_files=source_files,
            segments=[],
        )
        reserved_catalog_bytes = len(self._serializer.serialize(draft_catalog))

        if reserved_catalog_bytes >= config.tape_nominal_capacity_bytes:
            raise BackupPlanError(
                f"Reserved catalog size ({reserved_catalog_bytes} bytes) equals or exceeds "
                f"tape nominal capacity ({config.tape_nominal_capacity_bytes} bytes); "
                "cannot allocate any file data."
            )

        usable_capacity = config.tape_nominal_capacity_bytes - reserved_catalog_bytes

        # Pass 2 — allocate files to tapes sequentially, splitting as needed.
        tapes: list[Tape] = []
        segments: list[TapeSegment] = []
        tape_seq = 0
        tape_offset = 0
        current_tape: Tape | None = None

        for source_file in source_files:
            remaining_in_file = source_file.size_bytes
            source_offset = 0
            seg_seq = 0

            while remaining_in_file > 0:
                if current_tape is None or tape_offset >= usable_capacity:
                    tape_seq += 1
                    tape_offset = 0
                    current_tape = Tape(
                        tape_id=f"TAPE-{backup_set_id}-{tape_seq:03d}",
                        backup_set_id=backup_set_id,
                        sequence_number=tape_seq,
                        nominal_capacity_bytes=config.tape_nominal_capacity_bytes,
                        reserved_catalog_bytes=reserved_catalog_bytes,
                    )
                    tapes.append(current_tape)
                    logger.debug(
                        "Allocated tape %s (sequence=%d, usable_capacity=%d bytes)",
                        current_tape.tape_id,
                        tape_seq,
                        usable_capacity,
                    )

                assert current_tape is not None  # guaranteed by the block above
                remaining_on_tape = usable_capacity - tape_offset
                chunk = min(remaining_in_file, remaining_on_tape)
                seg_seq += 1
                segment_id = f"SEG-{source_file.file_id}-{seg_seq:03d}"

                segment = TapeSegment(
                    segment_id=segment_id,
                    file_id=source_file.file_id,
                    tape_id=current_tape.tape_id,
                    tape_offset=tape_offset,
                    source_offset=source_offset,
                    length_bytes=chunk,
                    sha256="",
                )
                segments.append(segment)

                logger.debug(
                    "Segment %s: file=%s tape=%s tape_offset=%d "
                    "source_offset=%d length=%d",
                    segment_id,
                    source_file.file_id,
                    current_tape.tape_id,
                    tape_offset,
                    source_offset,
                    chunk,
                )

                tape_offset += chunk
                source_offset += chunk
                remaining_in_file -= chunk

        total_bytes = sum(f.size_bytes for f in source_files)
        logger.info(
            "Backup plan created: backup_set_id=%s tapes=%d files=%d total_bytes=%d",
            backup_set_id,
            len(tapes),
            len(source_files),
            total_bytes,
        )

        return BackupPlan(
            backup_set_id=backup_set_id,
            source_root=str(config.source_root),
            tapes=tapes,
            source_files=source_files,
            segments=segments,
        )
