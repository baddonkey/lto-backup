from dataclasses import dataclass, field


@dataclass(frozen=True)
class ContainerCheck:
    """Result of verifying a single container during post-backup verification."""

    container_id: str
    passed: bool
    errors: list[str] = field(default_factory=list)
