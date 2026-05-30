from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from lto_backup.exceptions.file_read_error import FileReadError
from lto_backup.infrastructure.filesystem.retry_file_system import RetryFileSystem
from lto_backup.interfaces.file_system import FileSystem

_PATH = Path("/src/doc.txt")
_OFFSET = 0
_LENGTH = 1024
_DATA = b"hello world"


def _make_fake_inner() -> FileSystem:
    """Return a MagicMock that satisfies the FileSystem protocol."""
    fake: FileSystem = MagicMock(spec=FileSystem)
    return fake


class TestRetryFileSystemDelegation:
    """Non-read methods are passed through unchanged."""

    def setup_method(self) -> None:
        self._inner = _make_fake_inner()
        self._svc = RetryFileSystem(self._inner, max_attempts=3, delay_seconds=0.0)

    def test_list_files_delegates(self) -> None:
        self._inner.list_files.return_value = [_PATH]  # type: ignore[attr-defined]

        result = self._svc.list_files(Path("/src"))

        self._inner.list_files.assert_called_once_with(Path("/src"))  # type: ignore[attr-defined]
        assert result == [_PATH]

    def test_file_size_delegates(self) -> None:
        self._inner.file_size.return_value = 42  # type: ignore[attr-defined]

        result = self._svc.file_size(_PATH)

        self._inner.file_size.assert_called_once_with(_PATH)  # type: ignore[attr-defined]
        assert result == 42

    def test_modified_at_timestamp_delegates(self) -> None:
        self._inner.modified_at_timestamp.return_value = 1.0  # type: ignore[attr-defined]

        result = self._svc.modified_at_timestamp(_PATH)

        self._inner.modified_at_timestamp.assert_called_once_with(_PATH)  # type: ignore[attr-defined]
        assert result == 1.0

    def test_write_segment_delegates(self) -> None:
        self._svc.write_segment(_PATH, 0, _DATA)

        self._inner.write_segment.assert_called_once_with(_PATH, 0, _DATA)  # type: ignore[attr-defined]


class TestRetryFileSystemReadSegmentSuccess:
    """read_segment succeeds on first attempt — no retry needed."""

    def test_returns_data_on_first_attempt(self) -> None:
        inner = _make_fake_inner()
        inner.read_segment.return_value = _DATA  # type: ignore[attr-defined]
        svc = RetryFileSystem(inner, max_attempts=3, delay_seconds=0.0)

        result = svc.read_segment(_PATH, _OFFSET, _LENGTH)

        assert result == _DATA

    def test_inner_called_exactly_once_on_success(self) -> None:
        inner = _make_fake_inner()
        inner.read_segment.return_value = _DATA  # type: ignore[attr-defined]
        svc = RetryFileSystem(inner, max_attempts=3, delay_seconds=0.0)

        svc.read_segment(_PATH, _OFFSET, _LENGTH)

        inner.read_segment.assert_called_once_with(_PATH, _OFFSET, _LENGTH)  # type: ignore[attr-defined]


class TestRetryFileSystemReadSegmentRetry:
    """read_segment retries on OSError and returns data when a later attempt succeeds."""

    def test_succeeds_after_one_failure(self) -> None:
        inner = _make_fake_inner()
        inner.read_segment.side_effect = [OSError("flake"), _DATA]  # type: ignore[attr-defined]
        svc = RetryFileSystem(inner, max_attempts=3, delay_seconds=0.0)

        result = svc.read_segment(_PATH, _OFFSET, _LENGTH)

        assert result == _DATA

    def test_inner_called_twice_when_first_fails(self) -> None:
        inner = _make_fake_inner()
        inner.read_segment.side_effect = [OSError("flake"), _DATA]  # type: ignore[attr-defined]
        svc = RetryFileSystem(inner, max_attempts=3, delay_seconds=0.0)

        svc.read_segment(_PATH, _OFFSET, _LENGTH)

        assert inner.read_segment.call_count == 2  # type: ignore[attr-defined]

    def test_sleep_called_between_retries(self) -> None:
        inner = _make_fake_inner()
        inner.read_segment.side_effect = [OSError("flake"), _DATA]  # type: ignore[attr-defined]
        svc = RetryFileSystem(inner, max_attempts=3, delay_seconds=0.5)

        with patch("lto_backup.infrastructure.filesystem.retry_file_system.time.sleep") as mock_sleep:
            svc.read_segment(_PATH, _OFFSET, _LENGTH)

        mock_sleep.assert_called_once_with(0.5)

    def test_no_sleep_when_delay_is_zero(self) -> None:
        inner = _make_fake_inner()
        inner.read_segment.side_effect = [OSError("flake"), _DATA]  # type: ignore[attr-defined]
        svc = RetryFileSystem(inner, max_attempts=3, delay_seconds=0.0)

        with patch("lto_backup.infrastructure.filesystem.retry_file_system.time.sleep") as mock_sleep:
            svc.read_segment(_PATH, _OFFSET, _LENGTH)

        mock_sleep.assert_not_called()

    def test_sleep_called_between_each_retry(self) -> None:
        inner = _make_fake_inner()
        inner.read_segment.side_effect = [OSError("a"), OSError("b"), _DATA]  # type: ignore[attr-defined]
        svc = RetryFileSystem(inner, max_attempts=3, delay_seconds=1.0)

        with patch("lto_backup.infrastructure.filesystem.retry_file_system.time.sleep") as mock_sleep:
            svc.read_segment(_PATH, _OFFSET, _LENGTH)

        assert mock_sleep.call_args_list == [call(1.0), call(1.0)]


class TestRetryFileSystemReadSegmentExhausted:
    """read_segment raises FileReadError after all attempts are exhausted."""

    def test_raises_file_read_error_when_exhausted(self) -> None:
        inner = _make_fake_inner()
        inner.read_segment.side_effect = OSError("persistent failure")  # type: ignore[attr-defined]
        svc = RetryFileSystem(inner, max_attempts=3, delay_seconds=0.0)

        with pytest.raises(FileReadError):
            svc.read_segment(_PATH, _OFFSET, _LENGTH)

    def test_inner_called_max_attempts_times(self) -> None:
        inner = _make_fake_inner()
        inner.read_segment.side_effect = OSError("persistent failure")  # type: ignore[attr-defined]
        svc = RetryFileSystem(inner, max_attempts=3, delay_seconds=0.0)

        with pytest.raises(FileReadError):
            svc.read_segment(_PATH, _OFFSET, _LENGTH)

        assert inner.read_segment.call_count == 3  # type: ignore[attr-defined]

    def test_error_message_includes_path(self) -> None:
        inner = _make_fake_inner()
        inner.read_segment.side_effect = OSError("disk error")  # type: ignore[attr-defined]
        svc = RetryFileSystem(inner, max_attempts=2, delay_seconds=0.0)

        with pytest.raises(FileReadError) as exc_info:
            svc.read_segment(_PATH, _OFFSET, _LENGTH)

        assert str(_PATH) in str(exc_info.value)

    def test_original_os_error_is_chained(self) -> None:
        original = OSError("disk error")
        inner = _make_fake_inner()
        inner.read_segment.side_effect = original  # type: ignore[attr-defined]
        svc = RetryFileSystem(inner, max_attempts=1, delay_seconds=0.0)

        with pytest.raises(FileReadError) as exc_info:
            svc.read_segment(_PATH, _OFFSET, _LENGTH)

        assert exc_info.value.__cause__ is original


class TestRetryFileSystemSingleAttempt:
    """max_attempts=1 means exactly one attempt — no retry at all."""

    def test_raises_immediately_on_failure(self) -> None:
        inner = _make_fake_inner()
        inner.read_segment.side_effect = OSError("gone")  # type: ignore[attr-defined]
        svc = RetryFileSystem(inner, max_attempts=1, delay_seconds=0.0)

        with pytest.raises(FileReadError):
            svc.read_segment(_PATH, _OFFSET, _LENGTH)

        assert inner.read_segment.call_count == 1  # type: ignore[attr-defined]

    def test_no_sleep_on_single_attempt_failure(self) -> None:
        inner = _make_fake_inner()
        inner.read_segment.side_effect = OSError("gone")  # type: ignore[attr-defined]
        svc = RetryFileSystem(inner, max_attempts=1, delay_seconds=1.0)

        with patch("lto_backup.infrastructure.filesystem.retry_file_system.time.sleep") as mock_sleep:
            with pytest.raises(FileReadError):
                svc.read_segment(_PATH, _OFFSET, _LENGTH)

        mock_sleep.assert_not_called()
