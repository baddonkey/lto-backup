import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class LinuxLtoTapeDrive:
    """Placeholder adapter for a real Linux LTO tape drive via /dev/nstX.

    Not implemented until the simulator flow is complete and validated.
    """

    def __init__(self, device: Path) -> None:
        self._device = device

    def load_tape(self, tape_id: str) -> None:
        raise NotImplementedError("Real LTO hardware not yet implemented")

    def unload_tape(self) -> None:
        raise NotImplementedError("Real LTO hardware not yet implemented")

    def current_tape_id(self) -> str:
        raise NotImplementedError("Real LTO hardware not yet implemented")

    def remaining_capacity_bytes(self) -> int:
        raise NotImplementedError("Real LTO hardware not yet implemented")

    def write_file(self, source_path: Path, destination_name: str) -> None:
        raise NotImplementedError("Real LTO hardware not yet implemented")

    def write_bytes(self, destination_name: str, data: bytes) -> None:
        raise NotImplementedError("Real LTO hardware not yet implemented")

    def read_file(self, name: str) -> bytes:
        raise NotImplementedError("Real LTO hardware not yet implemented")

    def list_files(self) -> list[str]:
        raise NotImplementedError("Real LTO hardware not yet implemented")
