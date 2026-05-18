from typing import Protocol

from lto_backup.domain.tape import Tape


class TapeInventory(Protocol):
    def next_tape(self, backup_set_id: str, sequence_number: int) -> Tape: ...

    def all_tapes(self, backup_set_id: str) -> list[Tape]: ...
