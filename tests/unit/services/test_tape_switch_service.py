from unittest.mock import MagicMock

import pytest

from lto_backup.exceptions.tape_not_loaded_error import TapeNotLoadedError
from lto_backup.interfaces.tape_drive import TapeDrive
from lto_backup.interfaces.user_prompt import UserPrompt
from lto_backup.services.tape_switch_service import TapeSwitchService


def _make_drive(fail_times: int = 0) -> TapeDrive:
    """Return a fake TapeDrive whose load_tape raises TapeNotLoadedError *fail_times* then succeeds."""
    drive = MagicMock(spec=TapeDrive)
    call_count = {"n": 0}

    def load_tape(tape_id: str) -> None:
        call_count["n"] += 1
        if call_count["n"] <= fail_times:
            raise TapeNotLoadedError(f"load failed (attempt {call_count['n']})")

    drive.load_tape.side_effect = load_tape
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
