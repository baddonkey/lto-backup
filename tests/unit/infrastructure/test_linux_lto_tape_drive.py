import errno
import shutil
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, call, patch

import pytest

from lto_backup.exceptions.tape_full_error import TapeFullError
from lto_backup.exceptions.tape_not_loaded_error import TapeNotLoadedError
from lto_backup.infrastructure.tape.linux_lto_tape_drive import LinuxLtoTapeDrive

_DEVICE = Path("/dev/nst0")
_MOUNT = Path("/mnt/lto_tape")
_TAPE_ID = "BACKUP-001"


def _ok() -> CompletedProcess[str]:
    return CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _fail(stderr: str = "error") -> CompletedProcess[str]:
    return CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


class TestLinuxLtoTapeDriveLoadTape:
    def test_load_tape_calls_ltfs_with_correct_args(self, tmp_path: Path) -> None:
        drive = LinuxLtoTapeDrive(_DEVICE, tmp_path)
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            drive.load_tape(_TAPE_ID)

        mock_run.assert_called_once_with(
            ["ltfs", "-o", f"devname={_DEVICE}", str(tmp_path)],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_load_tape_raises_tape_not_loaded_on_nonzero_exit(self, tmp_path: Path) -> None:
        drive = LinuxLtoTapeDrive(_DEVICE, tmp_path)
        with patch("subprocess.run", return_value=_fail("mount failed")):
            with pytest.raises(TapeNotLoadedError):
                drive.load_tape(_TAPE_ID)

    def test_load_tape_raises_on_id_mismatch(self, tmp_path: Path) -> None:
        tape_id_file = tmp_path / ".tape_id"
        tape_id_file.write_text("DIFFERENT-TAPE")
        drive = LinuxLtoTapeDrive(_DEVICE, tmp_path)
        with patch("subprocess.run", return_value=_ok()):
            with pytest.raises(TapeNotLoadedError, match="mismatch"):
                drive.load_tape(_TAPE_ID)


class TestLinuxLtoTapeDriveUnloadTape:
    def _loaded_drive(self, tmp_path: Path) -> LinuxLtoTapeDrive:
        drive = LinuxLtoTapeDrive(_DEVICE, tmp_path)
        with patch("subprocess.run", return_value=_ok()):
            drive.load_tape(_TAPE_ID)
        return drive

    def test_unload_tape_calls_umount_and_mt(self, tmp_path: Path) -> None:
        drive = self._loaded_drive(tmp_path)
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            drive.unload_tape()

        assert mock_run.call_count == 2
        calls = mock_run.call_args_list
        assert calls[0] == call(
            ["umount", str(tmp_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert calls[1] == call(
            ["mt", "-f", str(_DEVICE), "offline"],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_unload_tape_raises_if_not_mounted(self, tmp_path: Path) -> None:
        drive = LinuxLtoTapeDrive(_DEVICE, tmp_path)
        with pytest.raises(TapeNotLoadedError):
            drive.unload_tape()

    def test_unload_tape_raises_on_umount_failure(self, tmp_path: Path) -> None:
        drive = self._loaded_drive(tmp_path)
        with patch("subprocess.run", return_value=_fail("device busy")):
            with pytest.raises(TapeNotLoadedError):
                drive.unload_tape()


class TestLinuxLtoTapeDriveWriteBytes:
    def _loaded_drive(self, tmp_path: Path) -> LinuxLtoTapeDrive:
        drive = LinuxLtoTapeDrive(_DEVICE, tmp_path)
        with patch("subprocess.run", return_value=_ok()):
            drive.load_tape(_TAPE_ID)
        return drive

    def test_write_bytes_raises_tape_full_on_enospc(self, tmp_path: Path) -> None:
        drive = self._loaded_drive(tmp_path)
        enospc = OSError(errno.ENOSPC, "No space left on device")
        with patch("pathlib.Path.write_bytes", side_effect=enospc):
            with pytest.raises(TapeFullError):
                drive.write_bytes("file.bin", b"data")

    def test_write_bytes_succeeds_and_file_is_readable(self, tmp_path: Path) -> None:
        drive = self._loaded_drive(tmp_path)
        drive.write_bytes("hello.bin", b"hello")
        assert (tmp_path / "data" / "hello.bin").read_bytes() == b"hello"


class TestLinuxLtoTapeDriveCurrentTapeId:
    def test_current_tape_id_returns_correct_id_when_mounted(self, tmp_path: Path) -> None:
        drive = LinuxLtoTapeDrive(_DEVICE, tmp_path)
        with patch("subprocess.run", return_value=_ok()):
            drive.load_tape(_TAPE_ID)
        assert drive.current_tape_id() == _TAPE_ID

    def test_current_tape_id_raises_when_not_mounted(self, tmp_path: Path) -> None:
        drive = LinuxLtoTapeDrive(_DEVICE, tmp_path)
        with pytest.raises(TapeNotLoadedError):
            drive.current_tape_id()
