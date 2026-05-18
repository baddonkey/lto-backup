import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class Sha256FileHasher:
    """Computes SHA-256 checksums for files and byte buffers."""

    def hash_file(self, path: Path) -> str:
        logger.debug("Hashing file: %s", path)
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        hexdigest = digest.hexdigest()
        logger.debug("SHA-256 of %s: %s", path, hexdigest)
        return hexdigest

    def hash_bytes(self, data: bytes) -> str:
        hexdigest = hashlib.sha256(data).hexdigest()
        logger.debug("SHA-256 of %d bytes: %s", len(data), hexdigest)
        return hexdigest
