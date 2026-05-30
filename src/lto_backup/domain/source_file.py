from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SourceFile:
    file_id: str
    relative_path: str
    absolute_path: str
    size_bytes: int
    sha256: str
    modified_at: datetime
    unix_mode: int | None = field(default=None)
