import logging
from pathlib import Path

from lto_backup.exceptions.tape_full_error import TapeFullError
from lto_backup.exceptions.tape_not_loaded_error import TapeNotLoadedError
from lto_backup.infrastructure.simulator.simulator_failure_config import SimulatorFailureConfig
from lto_backup.infrastructure.simulator.virtual_tape import VirtualTape

logger = logging.getLogger(__name__)


class SimulatorTapeDrive:
    """Simulated LTO tape drive that stores virtual tapes as directories on disk."""

    def __init__(
        self,
        tapes_root: Path,
        tape_capacity_bytes: int,
        failure_config: SimulatorFailureConfig | None = None,
    ) -> None:
        self._tapes_root = tapes_root
        self._tape_capacity_bytes = tape_capacity_bytes
        self._failure_config = failure_config or SimulatorFailureConfig()
        self._loaded_tape: VirtualTape | None = None

    # ------------------------------------------------------------------
    # TapeDrive protocol
    # ------------------------------------------------------------------

    def load_tape(self, tape_id: str) -> None:
        if self._failure_config.fail_on_load or tape_id in self._failure_config.failed_tape_ids:
            logger.error("Load rejected for tape %s: %s", tape_id, self._failure_config.error_message)
            raise TapeNotLoadedError(self._failure_config.error_message)
        tape_dir = self._tapes_root / tape_id
        self._loaded_tape = VirtualTape(tape_id, tape_dir, self._tape_capacity_bytes)
        logger.info("Loaded tape %s (capacity %d bytes)", tape_id, self._tape_capacity_bytes)

    def unload_tape(self) -> None:
        self._require_tape()
        tape_id = self._loaded_tape.tape_id  # type: ignore[union-attr]
        logger.info(
            "Unloaded tape %s (%d bytes written, %d remaining)",
            tape_id,
            self._loaded_tape.bytes_written,  # type: ignore[union-attr]
            self._loaded_tape.remaining_bytes,  # type: ignore[union-attr]
        )
        self._loaded_tape = None

    def current_tape_id(self) -> str:
        self._require_tape()
        return self._loaded_tape.tape_id  # type: ignore[union-attr]

    def remaining_capacity_bytes(self) -> int:
        self._require_tape()
        return self._loaded_tape.remaining_bytes  # type: ignore[union-attr]

    def write_file(self, source_path: Path, destination_name: str) -> None:
        data = source_path.read_bytes()
        self.write_bytes(destination_name, data)

    def write_bytes(self, destination_name: str, data: bytes) -> None:
        tape = self._require_tape()
        if self._failure_config.fail_on_write:
            logger.warning(
                "Write of %s rejected by failure injection on tape %s",
                destination_name,
                tape.tape_id,
            )
            raise TapeFullError(self._failure_config.error_message)
        threshold = self._failure_config.fail_after_bytes_written
        if threshold is not None and tape.bytes_written + len(data) > threshold:
            logger.warning(
                "Write of %s would exceed injected threshold (%d bytes) on tape %s",
                destination_name,
                threshold,
                tape.tape_id,
            )
            raise TapeFullError(self._failure_config.error_message)
        if len(data) > tape.remaining_bytes:
            logger.error(
                "Tape full: cannot write %d bytes to %s on tape %s (%d bytes remaining)",
                len(data),
                destination_name,
                tape.tape_id,
                tape.remaining_bytes,
            )
            raise TapeFullError(
                f"Write of {len(data)} bytes exceeds remaining capacity "
                f"{tape.remaining_bytes} on tape {tape.tape_id}"
            )
        tape.write(destination_name, data)
        logger.debug(
            "Wrote %d bytes as %s on tape %s (%d remaining)",
            len(data),
            destination_name,
            tape.tape_id,
            tape.remaining_bytes,
        )

    def read_file(self, name: str) -> bytes:
        tape = self._require_tape()
        if self._failure_config.fail_on_read:
            logger.error("Read of %s rejected by failure injection on tape %s", name, tape.tape_id)
            raise OSError(self._failure_config.error_message)
        data = tape.read(name)
        logger.debug("Read %d bytes from %s on tape %s", len(data), name, tape.tape_id)
        return data

    def list_files(self) -> list[str]:
        return self._require_tape().list_files()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_tape(self) -> VirtualTape:
        if self._loaded_tape is None:
            raise TapeNotLoadedError("No tape is currently loaded")
        return self._loaded_tape
