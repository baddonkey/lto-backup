from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SourceFile:
    file_id: str
    relative_path: str
    absolute_path: str
    size_bytes: int
    sha256: str
    modified_at: datetime
