from dataclasses import dataclass, field

from lto_backup.domain.container_restore_result import ContainerRestoreResult


@dataclass(frozen=True)
class RestoreReport:
    """Summary of a restore operation."""

    files_requested: int
    files_restored: int
    errors: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)
    container_results: list[ContainerRestoreResult] = field(default_factory=list)
