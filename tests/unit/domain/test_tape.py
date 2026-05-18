from dataclasses import FrozenInstanceError

import pytest

from lto_backup.domain.tape import Tape


def make_tape(
    *,
    nominal_capacity_bytes: int = 12_000_000_000,
    reserved_catalog_bytes: int = 100_000_000,
) -> Tape:
    return Tape(
        tape_id="TAPE-001",
        backup_set_id="BSET-001",
        sequence_number=1,
        nominal_capacity_bytes=nominal_capacity_bytes,
        reserved_catalog_bytes=reserved_catalog_bytes,
    )


class TestTapeUsableCapacity:
    def test_usable_capacity_subtracts_catalog_reserve(self) -> None:
        tape = make_tape(
            nominal_capacity_bytes=1_000,
            reserved_catalog_bytes=100,
        )
        assert tape.usable_capacity_bytes() == 900

    def test_usable_capacity_when_reserved_is_zero(self) -> None:
        tape = make_tape(
            nominal_capacity_bytes=1_000,
            reserved_catalog_bytes=0,
        )
        assert tape.usable_capacity_bytes() == 1_000

    def test_usable_capacity_realistic_lto9(self) -> None:
        # LTO-9: 18 TB nominal, catalog reserve measured from a draft catalog
        tape = make_tape(
            nominal_capacity_bytes=18 * 1024**4,
            reserved_catalog_bytes=500 * 1024**2,
        )
        expected = 18 * 1024**4 - 500 * 1024**2
        assert tape.usable_capacity_bytes() == expected

    def test_tape_is_immutable(self) -> None:
        tape = make_tape()
        with pytest.raises(FrozenInstanceError):
            tape.tape_id = "MODIFIED"  # type: ignore[misc]
