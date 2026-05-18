from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BackupConfig:
    source_root: Path
    tapes_root: Path
    tape_nominal_capacity_bytes: int
    max_container_size_bytes: int
