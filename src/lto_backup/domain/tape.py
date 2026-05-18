from dataclasses import dataclass


@dataclass(frozen=True)
class Tape:
    tape_id: str
    backup_set_id: str
    sequence_number: int
    nominal_capacity_bytes: int
    reserved_catalog_bytes: int

    def usable_capacity_bytes(self) -> int:
        return self.nominal_capacity_bytes - self.reserved_catalog_bytes
