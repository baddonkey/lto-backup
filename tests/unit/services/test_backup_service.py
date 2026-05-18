"""Unit tests for BackupService."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from lto_backup.config.backup_config import BackupConfig
from lto_backup.domain.backup_plan import BackupPlan
from lto_backup.domain.catalog import Catalog
from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape import Tape
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.exceptions.file_write_error import FileWriteError
from lto_backup.services.backup_service import BackupService


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

_FIXED_TIME = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


class FakeScanner:
    def __init__(self, source_files: list[SourceFile]) -> None:
        self._source_files = source_files
        self.scanned_path: Path | None = None

    def scan(self, source_root: Path) -> list[SourceFile]:
        self.scanned_path = source_root
        return self._source_files


class FakePlanner:
    def __init__(self, plan: BackupPlan) -> None:
        self._plan = plan
        self.planned_files: list[SourceFile] | None = None
        self.planned_config: BackupConfig | None = None

    def plan(self, source_files: list[SourceFile], config: BackupConfig) -> BackupPlan:
        self.planned_files = source_files
        self.planned_config = config
        return self._plan


class FakeWriter:
    def __init__(self) -> None:
        self.written_plan: BackupPlan | None = None
        self.raise_on_write: Exception | None = None

    def write(self, plan: BackupPlan) -> dict[str, str]:
        if self.raise_on_write is not None:
            raise self.raise_on_write
        self.written_plan = plan
        return {}


class FakeCatalogService:
    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog
        self.build_calls: list[BackupPlan] = []
        self.write_calls: int = 0

    def build_catalog(self, plan: BackupPlan, segment_sha256s: dict[str, str]) -> Catalog:
        self.build_calls.append(plan)
        return self._catalog

    def write_catalog_to_tape(self, catalog: Catalog, tape_drive: object) -> None:
        self.write_calls += 1


class FakeTapeDrive:
    def __init__(self) -> None:
        self.load_calls: list[str] = []
        self.unload_calls: int = 0

    def load_tape(self, tape_id: str) -> None:
        self.load_calls.append(tape_id)

    def unload_tape(self) -> None:
        self.unload_calls += 1

    def current_tape_id(self) -> str:
        return self.load_calls[-1] if self.load_calls else ""

    def remaining_capacity_bytes(self) -> int:
        return 2**40

    def write_file(self, source_path: Path, destination_name: str) -> None:
        raise NotImplementedError

    def write_bytes(self, destination_name: str, data: bytes) -> None:
        pass

    def read_file(self, name: str) -> bytes:
        return b""

    def list_files(self) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source_file() -> SourceFile:
    return SourceFile(
        file_id="file-001",
        relative_path="records/doc.txt",
        absolute_path="/src/records/doc.txt",
        size_bytes=100,
        sha256="abc123",
        modified_at=_FIXED_TIME,
    )


def _make_tape(tape_id: str, seq: int = 1) -> Tape:
    return Tape(
        tape_id=tape_id,
        backup_set_id="bset-001",
        sequence_number=seq,
        nominal_capacity_bytes=1_000_000,
        reserved_catalog_bytes=1_000,
    )


def _make_segment(tape_id: str, file_id: str = "file-001") -> TapeSegment:
    return TapeSegment(
        segment_id="seg-001",
        file_id=file_id,
        tape_id=tape_id,
        tape_offset=0,
        source_offset=0,
        length_bytes=100,
        sha256="abc123",
    )


def _make_config() -> BackupConfig:
    return BackupConfig(
        source_root=Path("/src"),
        tapes_root=Path("/tapes"),
        tape_nominal_capacity_bytes=1_000_000,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBackupService:
    def setup_method(self) -> None:
        self._tape1 = _make_tape("TAPE-001", seq=1)
        self._tape2 = _make_tape("TAPE-002", seq=2)
        self._source_file = _make_source_file()
        self._segment1 = _make_segment("TAPE-001")
        self._plan = BackupPlan(
            backup_set_id="bset-001",
            source_root="/src",
            tapes=[self._tape1, self._tape2],
            source_files=[self._source_file],
            segments=[self._segment1],
        )
        self._catalog = Catalog(
            schema_version="1.0",
            backup_set_id="bset-001",
            created_at=_FIXED_TIME,
            source_root="/src",
            tapes=[self._tape1, self._tape2],
            source_files=[self._source_file],
            segments=[self._segment1],
        )
        self._scanner = FakeScanner(source_files=[self._source_file])
        self._planner = FakePlanner(plan=self._plan)
        self._writer = FakeWriter()
        self._catalog_service = FakeCatalogService(catalog=self._catalog)
        self._tape_drive = FakeTapeDrive()
        self._service = BackupService(  # type: ignore[arg-type]
            scanner=self._scanner,  # type: ignore[arg-type]
            planner=self._planner,  # type: ignore[arg-type]
            writer=self._writer,  # type: ignore[arg-type]
            catalog_service=self._catalog_service,  # type: ignore[arg-type]
            tape_drive=self._tape_drive,
        )

    def test_run_returns_catalog_from_catalog_service(self) -> None:
        catalog = self._service.run(_make_config())

        assert catalog is self._catalog

    def test_run_passes_source_root_to_scanner(self) -> None:
        config = _make_config()
        self._service.run(config)

        assert self._scanner.scanned_path == config.source_root

    def test_run_passes_scanned_files_to_planner(self) -> None:
        self._service.run(_make_config())

        assert self._planner.planned_files == [self._source_file]

    def test_run_passes_plan_to_writer(self) -> None:
        self._service.run(_make_config())

        assert self._writer.written_plan is self._plan

    def test_run_writes_catalog_to_every_tape_in_plan(self) -> None:
        self._service.run(_make_config())

        assert self._catalog_service.write_calls == 2

    def test_run_loads_and_unloads_each_tape_for_catalog(self) -> None:
        self._service.run(_make_config())

        assert self._tape_drive.load_calls == ["TAPE-001", "TAPE-002"]
        assert self._tape_drive.unload_calls == 2

    def test_run_propagates_backup_error_from_writer(self) -> None:
        self._writer.raise_on_write = FileWriteError("simulated tape full")

        with pytest.raises(FileWriteError):
            self._service.run(_make_config())

    def test_run_does_not_write_catalog_when_writer_raises(self) -> None:
        self._writer.raise_on_write = FileWriteError("simulated tape full")

        with pytest.raises(FileWriteError):
            self._service.run(_make_config())

        assert self._catalog_service.write_calls == 0

    def test_run_builds_catalog_from_plan(self) -> None:
        self._service.run(_make_config())

        assert len(self._catalog_service.build_calls) == 1
        assert self._catalog_service.build_calls[0] is self._plan
