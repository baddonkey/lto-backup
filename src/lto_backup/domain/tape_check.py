from dataclasses import dataclass, field

from lto_backup.domain.container_check import ContainerCheck


@dataclass(frozen=True)
class TapeCheck:
    """Result of verifying a single tape during post-backup verification."""

    tape_id: str
    sequence_number: int
    catalog_checksum_passed: bool
    catalog_error: str | None
    containers: list[ContainerCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.catalog_checksum_passed and all(c.passed for c in self.containers)
