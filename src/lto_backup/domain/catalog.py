from dataclasses import dataclass, field
from datetime import datetime

from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape import Tape
from lto_backup.domain.tape_segment import TapeSegment


@dataclass(frozen=True)
class Catalog:
    schema_version: str
    backup_set_id: str
    created_at: datetime
    source_root: str
    tapes: list[Tape] = field(default_factory=list)
    source_files: list[SourceFile] = field(default_factory=list)
    segments: list[TapeSegment] = field(default_factory=list)
