from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class BackupConfig:
    source_root: Path
    tapes_root: Path
    tape_nominal_capacity_bytes: int
    max_container_size_bytes: int
    read_retry_attempts: int = field(default=1)
    read_retry_delay_seconds: float = field(default=0.0)
