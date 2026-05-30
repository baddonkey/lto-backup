from dataclasses import dataclass, field


@dataclass(frozen=True)
class RestoreReport:
    """Summary of a restore operation."""

    files_requested: int
    files_restored: int
    errors: list[str] = field(default_factory=list)
