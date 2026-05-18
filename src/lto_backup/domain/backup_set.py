from dataclasses import dataclass, field
from datetime import datetime

from lto_backup.domain.tape import Tape


@dataclass(frozen=True)
class BackupSet:
    backup_set_id: str
    source_root: str
    created_at: datetime
    tapes: list[Tape] = field(default_factory=list)
