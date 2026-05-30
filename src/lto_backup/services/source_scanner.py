import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from lto_backup.domain.source_file import SourceFile
from lto_backup.exceptions.backup_plan_error import BackupPlanError
from lto_backup.interfaces.clock import Clock
from lto_backup.interfaces.file_hasher import FileHasher
from lto_backup.interfaces.file_system import FileSystem

logger = logging.getLogger(__name__)

_APP_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


class SourceScanner:
    def __init__(
        self,
        file_system: FileSystem,
        file_hasher: FileHasher,
        clock: Clock,
    ) -> None:
        self._fs = file_system
        self._hasher = file_hasher
        self._clock = clock

    def scan(self, source_root: Path) -> list[SourceFile]:
        try:
            paths = self._fs.list_files(source_root)
        except OSError as exc:
            raise BackupPlanError(
                f"Source root does not exist or is not accessible: {source_root}"
            ) from exc

        results: list[SourceFile] = []
        total = len(paths)
        logger.info("SourceScanner: %d file(s) to scan", total)
        for absolute_path in paths:
            relative = absolute_path.relative_to(source_root)
            relative_posix = relative.as_posix()
            file_id = str(uuid.uuid5(_APP_NAMESPACE, relative_posix))
            size = self._fs.file_size(absolute_path)
            sha256 = self._hasher.hash_file(absolute_path)
            ts = self._fs.modified_at_timestamp(absolute_path)
            mode = self._fs.file_mode(absolute_path)
            modified_at = datetime.fromtimestamp(ts, tz=UTC)

            source_file = SourceFile(
                file_id=file_id,
                relative_path=relative_posix,
                absolute_path=absolute_path.as_posix(),
                size_bytes=size,
                sha256=sha256,
                modified_at=modified_at,
                unix_mode=mode,
            )
            logger.debug("Scanned file: %s", source_file)
            results.append(source_file)
            n = len(results)
            if n % 100 == 0:
                logger.info("SourceScanner: scanned %d / %d file(s) …", n, total)

        total_bytes = sum(f.size_bytes for f in results)
        logger.info(
            "Scan complete: %d file(s), %d byte(s) total",
            len(results),
            total_bytes,
        )
        return results
