import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class VirtualTape:
    """Represents a single simulated LTO tape stored as a directory on disk."""

    TAPE_META_FILENAME = "tape.json"

    def __init__(self, tape_id: str, root: Path, capacity_bytes: int) -> None:
        self._tape_id = tape_id
        self._root = root
        self._capacity_bytes = capacity_bytes
        self._data_dir = root / "data"
        self._catalog_dir = root / "catalog"
        self._bytes_written: int = 0

        self._root.mkdir(parents=True, exist_ok=True)
        self._data_dir.mkdir(exist_ok=True)
        self._catalog_dir.mkdir(exist_ok=True)

        # Restore bytes_written from persisted metadata so that reloading a
        # partially-written tape keeps the correct remaining capacity.
        meta_path = self._root / self.TAPE_META_FILENAME
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            self._bytes_written = int(meta.get("bytes_written", 0))
        else:
            self._persist_meta()

        logger.info(
            "VirtualTape %s initialised at %s (capacity %d bytes, %d bytes already written)",
            tape_id,
            root,
            capacity_bytes,
            self._bytes_written,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def tape_id(self) -> str:
        return self._tape_id

    @property
    def capacity_bytes(self) -> int:
        return self._capacity_bytes

    @property
    def bytes_written(self) -> int:
        return self._bytes_written

    @property
    def remaining_bytes(self) -> int:
        return max(0, self._capacity_bytes - self._bytes_written)

    # ------------------------------------------------------------------
    # I/O helpers
    # ------------------------------------------------------------------

    def write(self, name: str, data: bytes) -> None:
        target = self._resolve_path(name)
        target.write_bytes(data)
        self._bytes_written += len(data)
        self._persist_meta()
        logger.debug(
            "VirtualTape %s: wrote %d bytes as '%s' (%d bytes remaining)",
            self._tape_id,
            len(data),
            name,
            self.remaining_bytes,
        )

    def read(self, name: str) -> bytes:
        data = self._resolve_path(name).read_bytes()
        logger.debug("VirtualTape %s: read %d bytes from '%s'", self._tape_id, len(data), name)
        return data

    def list_files(self) -> list[str]:
        return [p.name for p in self._data_dir.iterdir() if p.is_file()]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_path(self, name: str) -> Path:
        if name.startswith("catalog/"):
            return self._catalog_dir / name[len("catalog/"):]
        return self._data_dir / name

    def _persist_meta(self) -> None:
        meta = {
            "tape_id": self._tape_id,
            "capacity_bytes": self._capacity_bytes,
            "bytes_written": self._bytes_written,
        }
        (self._root / self.TAPE_META_FILENAME).write_text(json.dumps(meta, indent=2))
