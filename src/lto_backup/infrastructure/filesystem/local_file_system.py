import logging
import os
import stat as _stat
from pathlib import Path

from lto_backup.exceptions.file_write_error import FileWriteError

logger = logging.getLogger(__name__)


class LocalFileSystem:
    """Concrete adapter that reads from the local filesystem."""

    def list_files(self, root: Path) -> list[Path]:
        logger.debug("Scanning directory: %s", root)
        files = sorted(p for p in root.rglob("*") if p.is_file())
        logger.info("Found %d file(s) under %s", len(files), root)
        return files

    def file_size(self, path: Path) -> int:
        size = path.stat().st_size
        logger.debug("File size %s: %d bytes", path, size)
        return size

    def file_mode(self, path: Path) -> int:
        return path.stat().st_mode

    def modified_at_timestamp(self, path: Path) -> float:
        return path.stat().st_mtime

    def read_segment(self, path: Path, offset: int, length: int) -> bytes:
        logger.debug("Reading %d bytes at offset %d from %s", length, offset, path)
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read(length)
        logger.debug("Read %d bytes from %s", len(data), path)
        return data

    def write_segment(self, path: Path, offset: int, data: bytes) -> None:
        logger.debug("Writing %d bytes at offset %d to %s", len(data), offset, path)
        try:
            if offset == 0:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("wb") as fh:
                    fh.write(data)
            else:
                with path.open("r+b") as fh:
                    fh.seek(offset)
                    fh.write(data)
        except OSError as exc:
            logger.error("Failed to write to %s: %s", path, exc)
            raise FileWriteError(f"Cannot write to {path}: {exc}") from exc
        logger.debug("Wrote %d bytes to %s at offset %d", len(data), path, offset)

    def set_attributes(
        self, path: Path, mtime_timestamp: float, unix_mode: int | None
    ) -> None:
        os.utime(path, (mtime_timestamp, mtime_timestamp))
        if unix_mode is not None:
            os.chmod(path, _stat.S_IMODE(unix_mode))
