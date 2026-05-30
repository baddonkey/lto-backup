from dataclasses import dataclass


@dataclass(frozen=True)
class ContainerRestoreResult:
    """SHA-256 verification outcome for a single container during restore."""

    container_id: str
    tape_id: str
    sha256_passed: bool
    error: str | None
