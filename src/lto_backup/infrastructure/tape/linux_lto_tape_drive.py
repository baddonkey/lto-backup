import errno
import logging
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

from lto_backup.exceptions.file_write_error import FileWriteError
from lto_backup.exceptions.tape_full_error import TapeFullError
from lto_backup.exceptions.tape_not_loaded_error import TapeNotLoadedError

logger = logging.getLogger(__name__)

_TAPE_ID_FILE = ".tape_id"
_DATA_DIR = "data"


class LinuxLtoTapeDrive:
    """LTO tape drive adapter using LTFS (Linear Tape File System).

    Mounts the tape at *mount_point* via ``ltfs`` and performs all I/O as
    regular filesystem operations.  Requires the ``ltfs``, ``mt``, and
    ``umount`` utilities to be installed on the host.
    """

    def __init__(self, device: Path, mount_point: Path) -> None:
        self._device = device
        self._mount_point = mount_point
        self._tape_id: str = ""
        self._mounted: bool = False

    # ------------------------------------------------------------------
    # TapeDrive protocol
    # ------------------------------------------------------------------

    def load_tape(self, tape_id: str) -> None:
        logger.debug("Mounting LTFS tape on %s via device %s", self._mount_point, self._device)
        result = subprocess.run(
            ["ltfs", "-o", f"devname={self._device}", str(self._mount_point)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(
                "ltfs mount failed (exit %d): %s", result.returncode, result.stderr
            )
            raise TapeNotLoadedError(
                f"Failed to mount LTFS tape: {result.stderr}"
            ) from RuntimeError(result.stderr)

        tape_id_path = self._mount_point / _TAPE_ID_FILE
        if tape_id_path.exists():
            stored_id = tape_id_path.read_text().strip()
            if stored_id and stored_id != tape_id:
                logger.error(
                    "Tape ID mismatch: expected %r, got %r — unmounting.", tape_id, stored_id
                )
                subprocess.run(
                    ["umount", str(self._mount_point)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                raise TapeNotLoadedError(
                    f"Tape ID mismatch: expected {tape_id!r}, found {stored_id!r} on tape."
                )
        else:
            tape_id_path.write_text(tape_id)

        self._tape_id = tape_id
        self._mounted = True
        logger.info("Tape %s loaded and mounted at %s", tape_id, self._mount_point)

    def unload_tape(self) -> None:
        self._require_mounted()
        logger.debug("Unmounting LTFS at %s", self._mount_point)

        umount_result = subprocess.run(
            ["umount", str(self._mount_point)],
            check=False,
            capture_output=True,
            text=True,
        )
        if umount_result.returncode != 0:
            logger.error("umount failed (exit %d): %s", umount_result.returncode, umount_result.stderr)
            raise TapeNotLoadedError(
                f"Failed to unmount tape: {umount_result.stderr}"
            ) from RuntimeError(umount_result.stderr)

        logger.debug("Ejecting tape via mt on %s", self._device)
        eject_result = subprocess.run(
            ["mt", "-f", str(self._device), "offline"],
            check=False,
            capture_output=True,
            text=True,
        )
        if eject_result.returncode != 0:
            logger.error("mt offline failed (exit %d): %s", eject_result.returncode, eject_result.stderr)
            self._mounted = False
            self._tape_id = ""
            raise TapeNotLoadedError(
                f"mt offline failed — tape may still be in drive: {eject_result.stderr}"
            ) from RuntimeError(eject_result.stderr)

        self._mounted = False
        self._tape_id = ""
        logger.info("Tape unloaded from %s", self._device)

    def current_tape_id(self) -> str:
        self._require_mounted()
        return self._tape_id

    def read_tape_id(self) -> str:
        self._require_mounted()
        tape_id_path = self._mount_point / _TAPE_ID_FILE
        if not tape_id_path.exists():
            return ""
        try:
            return tape_id_path.read_text().strip()
        except OSError:
            return ""

    def remaining_capacity_bytes(self) -> int:
        self._require_mounted()
        usage = shutil.disk_usage(self._mount_point)
        return usage.free

    def write_file(self, source_path: Path, destination_name: str) -> None:
        self._require_mounted()
        dest = self._data_dir() / destination_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source_path, dest)
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                raise TapeFullError(
                    f"No space left on tape while writing {destination_name}"
                ) from exc
            logger.error("write_file failed for %s: %s", destination_name, exc)
            raise FileWriteError(
                f"Failed to write {destination_name} to tape: {exc}"
            ) from exc
        logger.debug("Wrote file %s to tape at %s", source_path, dest)

    def write_bytes(self, destination_name: str, data: bytes) -> None:
        self._require_mounted()
        dest = self._data_dir() / destination_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            dest.write_bytes(data)
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                raise TapeFullError(
                    f"No space left on tape while writing {destination_name}"
                ) from exc
            logger.error("write_bytes failed for %s: %s", destination_name, exc)
            raise FileWriteError(
                f"Failed to write {destination_name} to tape: {exc}"
            ) from exc
        logger.debug("Wrote %d bytes as %s on tape", len(data), destination_name)

    def write_stream(
        self, destination_name: str, size_bytes: int, chunks: Iterator[bytes]
    ) -> None:
        self._require_mounted()
        dest = self._data_dir() / destination_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with dest.open("wb") as fh:
                for chunk in chunks:
                    fh.write(chunk)
                os.fsync(fh.fileno())
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                raise TapeFullError(
                    f"No space left on tape while writing {destination_name}"
                ) from exc
            logger.error("write_stream failed for %s: %s", destination_name, exc)
            raise FileWriteError(
                f"Failed to write {destination_name} to tape: {exc}"
            ) from exc
        logger.debug("Wrote %d bytes as %s on tape", size_bytes, destination_name)

    def read_file(self, name: str) -> bytes:
        self._require_mounted()
        path = self._data_dir() / name
        if not path.exists():
            raise FileNotFoundError(f"File {name!r} not found on tape")
        data = path.read_bytes()
        logger.debug("Read %d bytes from %s on tape", len(data), name)
        return data

    def read_file_segment(self, name: str, offset: int, length: int) -> bytes:
        self._require_mounted()
        path = self._data_dir() / name
        if not path.exists():
            raise FileNotFoundError(f"File {name!r} not found on tape")
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read(length)
        logger.debug(
            "Read %d bytes at offset %d from %s on tape", len(data), offset, name
        )
        return data

    def list_files(self) -> list[str]:
        self._require_mounted()
        data_dir = self._data_dir()
        if not data_dir.exists():
            return []
        return [p.name for p in data_dir.iterdir() if p.is_file()]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_mounted(self) -> None:
        if not self._mounted:
            raise TapeNotLoadedError("No tape is currently mounted")

    def _data_dir(self) -> Path:
        data_dir = self._mount_point / _DATA_DIR
        data_dir.mkdir(exist_ok=True)
        return data_dir
