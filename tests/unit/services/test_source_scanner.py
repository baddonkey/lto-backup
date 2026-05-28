import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lto_backup.domain.source_file import SourceFile
from lto_backup.exceptions.backup_plan_error import BackupPlanError
from lto_backup.services.source_scanner import _APP_NAMESPACE, SourceScanner


class FakeFileSystem:
    def __init__(
        self,
        files: dict[Path, tuple[int, float]],
        *,
        raise_on_list: OSError | None = None,
    ) -> None:
        self._files = files
        self._raise_on_list = raise_on_list

    def list_files(self, root: Path) -> list[Path]:
        if self._raise_on_list is not None:
            raise self._raise_on_list
        return list(self._files.keys())

    def file_size(self, path: Path) -> int:
        return self._files[path][0]

    def modified_at_timestamp(self, path: Path) -> float:
        return self._files[path][1]

    def read_segment(self, path: Path, offset: int, length: int) -> bytes:
        return b""


class FakeFileHasher:
    def __init__(self, hashes: dict[Path, str]) -> None:
        self._hashes = hashes

    def hash_file(self, path: Path) -> str:
        return self._hashes[path]

    def hash_bytes(self, data: bytes) -> str:
        return "fake"


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


_SOURCE_ROOT = Path("/backup/source")
_MTIME = 1_700_000_000.0


class TestSourceScanner:
    def _make_scanner(
        self,
        files: dict[Path, tuple[int, float]],
        hashes: dict[Path, str],
        *,
        raise_on_list: OSError | None = None,
    ) -> SourceScanner:
        fs = FakeFileSystem(files, raise_on_list=raise_on_list)
        hasher = FakeFileHasher(hashes)
        clock = FakeClock()
        return SourceScanner(fs, hasher, clock)

    def test_scan_returns_correct_source_file_list(self) -> None:
        path = _SOURCE_ROOT / "docs" / "report.pdf"
        scanner = self._make_scanner(
            {path: (1024, _MTIME)},
            {path: "abc123"},
        )

        result = scanner.scan(_SOURCE_ROOT)

        assert len(result) == 1
        assert isinstance(result[0], SourceFile)

    def test_scan_relative_path_is_posix_relative_to_source_root(self) -> None:
        path = _SOURCE_ROOT / "records" / "case-001" / "video.bin"
        scanner = self._make_scanner(
            {path: (100, _MTIME)},
            {path: "deadbeef"},
        )

        result = scanner.scan(_SOURCE_ROOT)

        assert result[0].relative_path == "records/case-001/video.bin"

    def test_scan_absolute_path_is_posix_string(self) -> None:
        path = _SOURCE_ROOT / "file.txt"
        scanner = self._make_scanner(
            {path: (10, _MTIME)},
            {path: "cafebabe"},
        )

        result = scanner.scan(_SOURCE_ROOT)

        assert result[0].absolute_path == str(path.as_posix())

    def test_scan_sha256_is_set_from_hasher(self) -> None:
        path = _SOURCE_ROOT / "data.bin"
        expected_hash = "e3b0c44298fc1c149afb"
        scanner = self._make_scanner(
            {path: (512, _MTIME)},
            {path: expected_hash},
        )

        result = scanner.scan(_SOURCE_ROOT)

        assert result[0].sha256 == expected_hash

    def test_scan_file_id_is_uuid5_of_relative_path(self) -> None:
        path = _SOURCE_ROOT / "sub" / "file.txt"
        scanner = self._make_scanner(
            {path: (8, _MTIME)},
            {path: "hash"},
        )

        result = scanner.scan(_SOURCE_ROOT)

        expected_id = str(uuid.uuid5(_APP_NAMESPACE, "sub/file.txt"))
        assert result[0].file_id == expected_id

    def test_scan_modified_at_is_utc_aware(self) -> None:
        path = _SOURCE_ROOT / "file.txt"
        scanner = self._make_scanner(
            {path: (1, _MTIME)},
            {path: "hash"},
        )

        result = scanner.scan(_SOURCE_ROOT)

        assert result[0].modified_at.tzinfo is UTC

    def test_scan_modified_at_matches_timestamp(self) -> None:
        path = _SOURCE_ROOT / "file.txt"
        scanner = self._make_scanner(
            {path: (1, _MTIME)},
            {path: "hash"},
        )

        result = scanner.scan(_SOURCE_ROOT)

        expected = datetime.fromtimestamp(_MTIME, tz=UTC)
        assert result[0].modified_at == expected

    def test_scan_empty_directory_returns_empty_list(self) -> None:
        scanner = self._make_scanner({}, {})

        result = scanner.scan(_SOURCE_ROOT)

        assert result == []

    def test_scan_size_bytes_is_set(self) -> None:
        path = _SOURCE_ROOT / "big.bin"
        scanner = self._make_scanner(
            {path: (999_999, _MTIME)},
            {path: "hash"},
        )

        result = scanner.scan(_SOURCE_ROOT)

        assert result[0].size_bytes == 999_999

    def test_scan_nonexistent_root_raises_backup_plan_error(self) -> None:
        scanner = self._make_scanner(
            {},
            {},
            raise_on_list=OSError("No such file or directory"),
        )

        with pytest.raises(BackupPlanError):
            scanner.scan(Path("/nonexistent/root"))

    def test_scan_nonexistent_root_chains_original_exception(self) -> None:
        original = OSError("No such file or directory")
        scanner = self._make_scanner(
            {},
            {},
            raise_on_list=original,
        )

        with pytest.raises(BackupPlanError) as exc_info:
            scanner.scan(Path("/nonexistent/root"))

        assert exc_info.value.__cause__ is original
