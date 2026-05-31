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
            [
                "sudo", "-n", "ltfs",
                "-o", f"devname={_DEVICE}",
                "-o", "sync_type=unmount",
                str(tmp_path),
            ],
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

    def test_unload_tape_calls_umount(self, tmp_path: Path) -> None:
        drive = self._loaded_drive(tmp_path)
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            drive.unload_tape()

        cmd_list = [c.args[0] for c in mock_run.call_args_list]
        # First call must be umount; mt offline is no longer issued
        assert cmd_list[0] == ["sudo", "-n", "umount", str(tmp_path)]
        assert all("mt" not in str(c) for c in cmd_list)

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


class TestLinuxLtoTapeDriveLoadTapeIdMismatchCleanup:
    def test_load_tape_calls_umount_on_id_mismatch(self, tmp_path: Path) -> None:
        """LTFS is mounted before the mismatch is detected; it must be unmounted."""
        tape_id_file = tmp_path / ".tape_id"
        tape_id_file.write_text("DIFFERENT-TAPE")
        drive = LinuxLtoTapeDrive(_DEVICE, tmp_path)
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_: object) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(TapeNotLoadedError):
                drive.load_tape(_TAPE_ID)

        # First call mounts (sudo ltfs), second call must be sudo umount cleanup.
        assert len(calls) >= 2
        assert calls[0][:3] == ["sudo", "-n", "ltfs"]
        assert calls[1][:3] == ["sudo", "-n", "umount"]

    def test_load_tape_id_mismatch_leaves_drive_unmounted(self, tmp_path: Path) -> None:
        """After mismatch the drive object must not consider itself mounted."""
        tape_id_file = tmp_path / ".tape_id"
        tape_id_file.write_text("DIFFERENT-TAPE")
        drive = LinuxLtoTapeDrive(_DEVICE, tmp_path)
        with patch("subprocess.run", return_value=_ok()):
            with pytest.raises(TapeNotLoadedError):
                drive.load_tape(_TAPE_ID)
        with pytest.raises(TapeNotLoadedError):
            drive.current_tape_id()


class TestLinuxLtoTapeDriveWriteBytesSubdirectory:
    def _loaded_drive(self, tmp_path: Path) -> LinuxLtoTapeDrive:
        drive = LinuxLtoTapeDrive(_DEVICE, tmp_path)
        with patch("subprocess.run", return_value=_ok()):
            drive.load_tape(_TAPE_ID)
        return drive

    def test_write_bytes_creates_subdirectory_and_writes_file(self, tmp_path: Path) -> None:
        drive = self._loaded_drive(tmp_path)
        drive.write_bytes("catalog/catalog.json", b'{"test": true}')
        assert (tmp_path / "data" / "catalog" / "catalog.json").read_bytes() == b'{"test": true}'

    def test_write_bytes_creates_nested_subdirectory(self, tmp_path: Path) -> None:
        drive = self._loaded_drive(tmp_path)
        drive.write_bytes("a/b/c.bin", b"deep")
        assert (tmp_path / "data" / "a" / "b" / "c.bin").read_bytes() == b"deep"

    def test_write_stream_creates_subdirectory(self, tmp_path: Path) -> None:
        drive = self._loaded_drive(tmp_path)
        drive.write_stream("catalog/catalog.sha256", 5, iter([b"hello"]))
        assert (tmp_path / "data" / "catalog" / "catalog.sha256").read_bytes() == b"hello"


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
