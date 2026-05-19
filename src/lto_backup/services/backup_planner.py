"""BackupPlanner service — allocates source files to containers and tapes, producing a BackupPlan."""

import logging
import uuid

from lto_backup.config.backup_config import BackupConfig
from lto_backup.domain.backup_plan import BackupPlan
from lto_backup.domain.catalog import Catalog
from lto_backup.domain.container import Container
from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape import Tape
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.exceptions.backup_plan_error import BackupPlanError
from lto_backup.interfaces.catalog_serializer import CatalogSerializer
from lto_backup.interfaces.clock import Clock

_CATALOG_SCHEMA_VERSION = "2.0"

logger = logging.getLogger(__name__)


class BackupPlanner:
    """Allocates source files to containers and tapes using a three-pass algorithm."""

    def __init__(self, serializer: CatalogSerializer, clock: Clock) -> None:
        self._serializer = serializer
        self._clock = clock

    def plan(self, source_files: list[SourceFile], config: BackupConfig) -> BackupPlan:
        """Create a BackupPlan packing *source_files* into containers on tapes per *config*."""
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
            containers=[],
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

        if config.max_container_size_bytes <= 0:
            raise BackupPlanError(
                f"max_container_size_bytes must be > 0, "
                f"got {config.max_container_size_bytes}"
            )

        # Clamp container size to usable capacity so a single container always
        # fits on one tape; if the caller specified a larger value, reduce silently.
        effective_container_size = min(config.max_container_size_bytes, usable_capacity)
        if effective_container_size != config.max_container_size_bytes:
            logger.debug(
                "max_container_size_bytes (%d) clamped to usable_capacity (%d)",
                config.max_container_size_bytes,
                usable_capacity,
            )

        # Pass 2 — pack source files sequentially into containers.
        segment_stubs: list[TapeSegment] = []
        container_sizes: dict[str, int] = {}
        container_ids_in_order: list[str] = []

        container_seq = 0
        container_fill = 0
        current_container_id: str | None = None

        for source_file in source_files:
            remaining = source_file.size_bytes
            source_offset = 0
            seg_seq = 0

            while remaining > 0:
                if current_container_id is None or container_fill >= effective_container_size:
                    if current_container_id is not None:
                        container_sizes[current_container_id] = container_fill
                    container_seq += 1
                    current_container_id = f"CNT-{backup_set_id}-{container_seq:05d}"
                    container_ids_in_order.append(current_container_id)
                    container_fill = 0
                    logger.debug(
                        "Opened container %s (seq=%d)",
                        current_container_id,
                        container_seq,
                    )

                space_in_container = effective_container_size - container_fill
                chunk = min(remaining, space_in_container)
                seg_seq += 1
                segment_id = f"SEG-{source_file.file_id}-{seg_seq:03d}"

                segment = TapeSegment(
                    segment_id=segment_id,
                    file_id=source_file.file_id,
                    container_id=current_container_id,
                    container_offset=container_fill,
                    source_offset=source_offset,
                    length_bytes=chunk,
                    sha256="",
                )
                segment_stubs.append(segment)

                logger.debug(
                    "Segment %s: file=%s container=%s container_offset=%d "
                    "source_offset=%d length=%d",
                    segment_id,
                    source_file.file_id,
                    current_container_id,
                    container_fill,
                    source_offset,
                    chunk,
                )

                container_fill += chunk
                source_offset += chunk
                remaining -= chunk

        if current_container_id is not None:
            container_sizes[current_container_id] = container_fill

        # Pass 3 — assign containers to tapes sequentially.
        tapes: list[Tape] = []
        containers: list[Container] = []
        tape_seq = 0
        tape_offset = 0
        current_tape: Tape | None = None

        for global_seq, container_id in enumerate(container_ids_in_order, start=1):
            container_size = container_sizes[container_id]

            if current_tape is None or tape_offset + container_size > usable_capacity:
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

            container = Container(
                container_id=container_id,
                backup_set_id=backup_set_id,
                tape_id=current_tape.tape_id,
                sequence_number=global_seq,
                tape_offset=tape_offset,
                size_bytes=container_size,
            )
            containers.append(container)
            tape_offset += container_size

        total_bytes = sum(f.size_bytes for f in source_files)
        logger.info(
            "Backup plan created: backup_set_id=%s tapes=%d containers=%d "
            "files=%d total_bytes=%d",
            backup_set_id,
            len(tapes),
            len(containers),
            len(source_files),
            total_bytes,
        )

        return BackupPlan(
            backup_set_id=backup_set_id,
            source_root=str(config.source_root),
            tapes=tapes,
            containers=containers,
            source_files=source_files,
            segments=segment_stubs,
        )
