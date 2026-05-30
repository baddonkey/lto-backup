import logging
import time
from pathlib import Path

from lto_backup.exceptions.file_read_error import FileReadError
from lto_backup.interfaces.file_system import FileSystem

logger = logging.getLogger(__name__)


class RetryFileSystem:
    """FileSystem adapter that retries read_segment on OSError.

    All other operations are delegated to *inner* unchanged.
    Retry behaviour is controlled by *max_attempts* (total attempts, including
    the first) and *delay_seconds* (sleep between attempts).

    max_attempts=1 is equivalent to no retry — one attempt, raises on failure.
    """

    def __init__(
        self,
        inner: FileSystem,
        max_attempts: int,
        delay_seconds: float,
    ) -> None:
        self._inner = inner
        self._max_attempts = max(1, max_attempts)
        self._delay_seconds = delay_seconds

    # ------------------------------------------------------------------
    # Delegation — no retry for metadata or write operations
    # ------------------------------------------------------------------

    def list_files(self, root: Path) -> list[Path]:
        return self._inner.list_files(root)

    def file_size(self, path: Path) -> int:
        return self._inner.file_size(path)

    def file_mode(self, path: Path) -> int:
        return self._inner.file_mode(path)

    def modified_at_timestamp(self, path: Path) -> float:
        return self._inner.modified_at_timestamp(path)

    def write_segment(self, path: Path, offset: int, data: bytes) -> None:
        self._inner.write_segment(path, offset, data)

    def set_attributes(
        self, path: Path, mtime_timestamp: float, unix_mode: int | None
    ) -> None:
        self._inner.set_attributes(path, mtime_timestamp, unix_mode)

    # ------------------------------------------------------------------
    # Read with retry
    # ------------------------------------------------------------------

    def read_segment(self, path: Path, offset: int, length: int) -> bytes:
        last_exc: OSError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._inner.read_segment(path, offset, length)
            except OSError as exc:
                last_exc = exc
                if attempt < self._max_attempts:
                    logger.warning(
                        "read_segment failed (attempt %d/%d) for %s at offset %d: %s — retrying",
                        attempt,
                        self._max_attempts,
                        path,
                        offset,
                        exc,
                    )
                    if self._delay_seconds > 0:
                        time.sleep(self._delay_seconds)
                else:
                    logger.error(
                        "read_segment failed after %d attempt(s) for %s at offset %d: %s",
                        self._max_attempts,
                        path,
                        offset,
                        exc,
                    )
        raise FileReadError(
            f"Cannot read {path} at offset {offset} after {self._max_attempts} attempt(s)"
        ) from last_exc
