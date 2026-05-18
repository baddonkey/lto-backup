from pathlib import Path
from typing import Protocol


class FileHasher(Protocol):
    def hash_file(self, path: Path) -> str: ...

    def hash_bytes(self, data: bytes) -> str: ...
