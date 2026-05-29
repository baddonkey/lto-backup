from unittest.mock import MagicMock

import pytest

from lto_backup.exceptions.tape_not_loaded_error import TapeNotLoadedError
from lto_backup.exceptions.wrong_tape_error import WrongTapeError
from lto_backup.interfaces.tape_drive import TapeDrive
from lto_backup.interfaces.user_prompt import UserPrompt
from lto_backup.services.tape_switch_service import TapeSwitchService


def _make_drive(fail_times: int = 0, recorded_tape_id: str | None = None) -> TapeDrive:
    """Return a fake TapeDrive whose load_tape raises TapeNotLoadedError *fail_times* then succeeds.

    ``recorded_tape_id`` controls what ``read_tape_id`` returns. ``None`` mirrors the
    requested tape_id (identity matches), ``""`` simulates a freshly formatted tape,
    any other value simulates a mismatch.
    """
    drive = MagicMock(spec=TapeDrive)
    call_count = {"n": 0}
    requested: dict[str, str] = {}

    def load_tape(tape_id: str) -> None:
        call_count["n"] += 1
        if call_count["n"] <= fail_times:
            raise TapeNotLoadedError(f"load failed (attempt {call_count['n']})")
        requested["id"] = tape_id

    def read_tape_id() -> str:
        if recorded_tape_id is None:
            return requested.get("id", "")
        return recorded_tape_id

    drive.load_tape.side_effect = load_tape
    drive.read_tape_id.side_effect = read_tape_id
    return drive


def _make_prompt() -> UserPrompt:
    return MagicMock(spec=UserPrompt)


class TestTapeSwitchService:
    def test_successful_load_on_first_attempt(self) -> None:
        drive = _make_drive(fail_times=0)
        prompt = _make_prompt()
        service = TapeSwitchService(drive, prompt, max_retries=5)

        service.request_and_load("TAPE-001", 1)

        drive.load_tape.assert_called_once_with("TAPE-001")

    def test_successful_load_after_two_retries(self) -> None:
        drive = _make_drive(fail_times=2)
        prompt = _make_prompt()
        service = TapeSwitchService(drive, prompt, max_retries=5)

        service.request_and_load("TAPE-001", 1)

        assert drive.load_tape.call_count == 3

    def test_raises_tape_not_loaded_after_max_retries_exhausted(self) -> None:
        drive = _make_drive(fail_times=10)
        prompt = _make_prompt()
        service = TapeSwitchService(drive, prompt, max_retries=3)

        with pytest.raises(TapeNotLoadedError):
            service.request_and_load("TAPE-001", 1)

        # Called max_retries + 1 times (initial attempt + retries)
        assert drive.load_tape.call_count == 4

    def test_prompt_inform_called_before_each_attempt(self) -> None:
        drive = _make_drive(fail_times=2)
        prompt = _make_prompt()
        service = TapeSwitchService(drive, prompt, max_retries=5)

        service.request_and_load("TAPE-001", 1)

        # inform called once for initial attempt + once per retry (2 retries)
        assert prompt.inform.call_count == 3

    def test_max_retries_zero_raises_immediately_without_retry(self) -> None:
        drive = _make_drive(fail_times=10)
        prompt = _make_prompt()
        service = TapeSwitchService(drive, prompt, max_retries=0)

        with pytest.raises(TapeNotLoadedError):
            service.request_and_load("TAPE-001", 1)

        # Only the initial attempt is made — no retries
        assert drive.load_tape.call_count == 1

    def test_wrong_tape_raises_wrong_tape_error_and_unloads(self) -> None:
        drive = _make_drive(fail_times=0, recorded_tape_id="TAPE-999")
        prompt = _make_prompt()
        service = TapeSwitchService(drive, prompt, max_retries=5)

        with pytest.raises(WrongTapeError):
            service.request_and_load("TAPE-001", 1)

        drive.load_tape.assert_called_once_with("TAPE-001")
        drive.unload_tape.assert_called_once()

    def test_blank_recorded_tape_id_is_accepted(self) -> None:
        # Freshly formatted tape with no recorded identity loads successfully.
        drive = _make_drive(fail_times=0, recorded_tape_id="")
        prompt = _make_prompt()
        service = TapeSwitchService(drive, prompt, max_retries=5)

        service.request_and_load("TAPE-001", 1)

        drive.load_tape.assert_called_once_with("TAPE-001")
        drive.unload_tape.assert_not_called()
