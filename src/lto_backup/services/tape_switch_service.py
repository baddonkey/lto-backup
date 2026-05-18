import logging

from lto_backup.exceptions.tape_not_loaded_error import TapeNotLoadedError
from lto_backup.interfaces.tape_drive import TapeDrive
from lto_backup.interfaces.user_prompt import UserPrompt

logger = logging.getLogger(__name__)


class TapeSwitchService:
    """Handles interactive tape switching, prompting the operator and retrying on failure."""

    def __init__(
        self,
        tape_drive: TapeDrive,
        prompt: UserPrompt,
        max_retries: int = 5,
    ) -> None:
        self._tape_drive = tape_drive
        self._prompt = prompt
        self._max_retries = max_retries

    def request_and_load(self, tape_id: str, sequence_number: int) -> None:
        """Prompt the operator to insert a tape and load it, retrying on failure."""
        last_exc: TapeNotLoadedError | None = None

        for attempt in range(self._max_retries + 1):
            if attempt == 0:
                self._prompt.inform(
                    f"Please insert tape {tape_id} (tape {sequence_number}) and press Enter."
                )
            else:
                logger.warning(
                    "Tape load failed for %s (attempt %d/%d). Retrying.",
                    tape_id,
                    attempt,
                    self._max_retries,
                )
                self._prompt.inform(
                    f"Tape load failed. Please re-insert tape {tape_id} (attempt {attempt}/{self._max_retries}) and press Enter."
                )

            self._prompt.ask("")

            try:
                self._tape_drive.load_tape(tape_id)
                logger.info("Tape %s successfully loaded on attempt %d.", tape_id, attempt + 1)
                return
            except TapeNotLoadedError as exc:
                last_exc = exc

        raise TapeNotLoadedError(
            f"Failed to load tape {tape_id} after {self._max_retries} retries."
        ) from last_exc
