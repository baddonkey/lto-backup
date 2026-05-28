import logging
from pathlib import Path

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

    def modified_at_timestamp(self, path: Path) -> float:
        return path.stat().st_mtime

    def read_segment(self, path: Path, offset: int, length: int) -> bytes:
        logger.debug("Reading %d bytes at offset %d from %s", length, offset, path)
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read(length)
        logger.debug("Read %d bytes from %s", len(data), path)
        return data
