from dataclasses import dataclass, field

from lto_backup.domain.tape_check import TapeCheck


@dataclass(frozen=True)
class VerificationReport:
    """Structured outcome of a full post-backup verification run."""

    tape_checks: list[TapeCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.tape_checks) and all(tc.passed for tc in self.tape_checks)

    @property
    def errors(self) -> list[str]:
        result: list[str] = []
        for tc in self.tape_checks:
            if tc.catalog_error:
                result.append(tc.catalog_error)
            for cc in tc.containers:
                result.extend(cc.errors)
        return result
