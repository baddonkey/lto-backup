from pathlib import Path

import pytest

from lto_backup.exceptions.file_read_error import FileReadError
from lto_backup.infrastructure.simulator.simulator_failure_config import (
    SimulatorFailureConfig,
)
from lto_backup.infrastructure.simulator.simulator_tape_drive import (
    SimulatorTapeDrive,
)


def _make_drive(tapes_root: Path, *, fail_on_read: bool) -> SimulatorTapeDrive:
    return SimulatorTapeDrive(
        tapes_root=tapes_root,
        tape_capacity_bytes=10_000,
        failure_config=SimulatorFailureConfig(fail_on_read=fail_on_read),
    )


class TestSimulatorReadFailures:
    def test_read_file_raises_file_read_error_when_failure_injected(
        self, tmp_path: Path
    ) -> None:
        drive = _make_drive(tmp_path, fail_on_read=False)
        drive.load_tape("TAPE-001")
        drive.write_bytes("blob", b"payload")
        drive.unload_tape()

        failing = _make_drive(tmp_path, fail_on_read=True)
        failing.load_tape("TAPE-001")

        with pytest.raises(FileReadError):
            failing.read_file("blob")

    def test_read_file_segment_raises_file_read_error_when_failure_injected(
        self, tmp_path: Path
    ) -> None:
        drive = _make_drive(tmp_path, fail_on_read=False)
        drive.load_tape("TAPE-001")
        drive.write_bytes("blob", b"payload")
        drive.unload_tape()

        failing = _make_drive(tmp_path, fail_on_read=True)
        failing.load_tape("TAPE-001")

        with pytest.raises(FileReadError):
            failing.read_file_segment("blob", 0, 3)
