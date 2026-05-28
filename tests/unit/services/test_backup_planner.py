"""Unit tests for BackupPlanner service."""

import pytest
from datetime import datetime
from pathlib import Path
from pathlib import PurePath

from lto_backup.config.backup_config import BackupConfig
from lto_backup.domain.catalog import Catalog
from lto_backup.domain.source_file import SourceFile
from lto_backup.exceptions.backup_plan_error import BackupPlanError
from lto_backup.services.backup_planner import BackupPlanner


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCatalogSerializer:
    """Returns a fixed-size byte string regardless of catalog contents."""

    def __init__(self, size: int = 100) -> None:
        self._size = size

    def serialize(self, catalog: Catalog) -> bytes:
        return b"x" * self._size

    def deserialize(self, data: bytes) -> Catalog:
        raise NotImplementedError


class FakeClock:
    """Returns a fixed datetime."""

    def now(self) -> datetime:
        return datetime(2026, 1, 1, 0, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file(file_id: str, size: int) -> SourceFile:
    return SourceFile(
        file_id=file_id,
        relative_path=f"path/to/{file_id}",
        absolute_path=f"/src/path/to/{file_id}",
        size_bytes=size,
        sha256="abc123",
        modified_at=datetime(2026, 1, 1),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBackupPlanner:
    """Tests for BackupPlanner.plan()."""

    def setup_method(self) -> None:
        # Fake serializer returns 100 bytes → reserved = 100 + 64 (checksum) = 164
        # usable_capacity = 1000 - 164 = 836
        self.serializer = FakeCatalogSerializer(size=100)
        self.clock = FakeClock()
        self.planner = BackupPlanner(self.serializer, self.clock)
        self.config = BackupConfig(
            source_root=Path("/src"),
            tapes_root=Path("/tapes"),
            tape_nominal_capacity_bytes=1000,
            max_container_size_bytes=1000,  # clamped to usable (836) by planner
        )

    # SPEC planner test requirement #1
    def test_all_files_fit_on_one_tape(self) -> None:
        files = [_make_file("f1", 300), _make_file("f2", 400)]
        plan = self.planner.plan(files, self.config)
        assert len(plan.tapes) == 1
        assert len(plan.segments) == 2

    # SPEC planner test requirement #2
    def test_multiple_files_span_multiple_tapes(self) -> None:
        # usable=836; f1=800 fits, leaves 36; f2=800 → 36 on tape1, 764 on tape2
        files = [_make_file("f1", 800), _make_file("f2", 800)]
        plan = self.planner.plan(files, self.config)
        assert len(plan.tapes) == 2
        assert len(plan.segments) == 3  # f1:1 segment, f2:2 segments

    # SPEC planner test requirement #3
    def test_single_large_file_splits_across_tapes(self) -> None:
        # 2000 bytes, usable=836 → 836+836+328 across 3 tapes
        files = [_make_file("f1", 2000)]
        plan = self.planner.plan(files, self.config)
        assert len(plan.tapes) == 3
        assert len(plan.segments) == 3
        assert len(plan.segments) == 3

    # SPEC planner test requirement #4 — container_offset tracking on split file
    def test_single_large_file_container_offset_tracking(self) -> None:
        # usable=836 → effective_container_size=836; each segment fills its own container
        files = [_make_file("f1", 2000)]
        plan = self.planner.plan(files, self.config)
        seg0, seg1, seg2 = plan.segments

        assert seg0.container_offset == 0
        assert seg0.source_offset == 0
        assert seg0.length_bytes == 836

        assert seg1.container_offset == 0
        assert seg1.source_offset == 836
        assert seg1.length_bytes == 836

        assert seg2.container_offset == 0
        assert seg2.source_offset == 1672
        assert seg2.length_bytes == 328

    # SPEC planner test requirement #5 — two-pass catalog reserve
    def test_catalog_reserve_reduces_usable_capacity(self) -> None:
        files = [_make_file("f1", 100)]
        plan = self.planner.plan(files, self.config)
        assert len(plan.tapes) == 1
        assert plan.tapes[0].reserved_catalog_bytes > 0
        # FakeCatalogSerializer returns 100 bytes; planner adds 64 bytes for the
        # SHA-256 checksum file → total reserve = 164.
        assert plan.tapes[0].reserved_catalog_bytes == 164

    # SPEC planner test requirement #6 — invalid capacity
    def test_invalid_capacity_zero_raises_backup_plan_error(self) -> None:
        config = BackupConfig(
            source_root=Path("/src"),
            tapes_root=Path("/tapes"),
            tape_nominal_capacity_bytes=0,
            max_container_size_bytes=1000,
        )
        with pytest.raises(BackupPlanError):
            self.planner.plan([], config)

    def test_invalid_capacity_negative_raises_backup_plan_error(self) -> None:
        config = BackupConfig(
            source_root=Path("/src"),
            tapes_root=Path("/tapes"),
            tape_nominal_capacity_bytes=-1,
            max_container_size_bytes=1000,
        )
        with pytest.raises(BackupPlanError):
            self.planner.plan([], config)

    def test_catalog_too_large_for_tape_raises_backup_plan_error(self) -> None:
        # Serializer returns 1000 bytes = entire nominal capacity → no room for data
        big_serializer = FakeCatalogSerializer(size=1000)
        planner = BackupPlanner(big_serializer, self.clock)
        with pytest.raises(BackupPlanError):
            planner.plan([_make_file("f1", 100)], self.config)

    # Additional: container_offset is correct for consecutive segments in the same container
    def test_container_offset_consecutive_segments_same_container(self) -> None:
        files = [_make_file("f1", 300), _make_file("f2", 200)]
        plan = self.planner.plan(files, self.config)
        assert len(plan.tapes) == 1
        assert len(plan.containers) == 1
        assert plan.segments[0].container_offset == 0
        assert plan.segments[0].length_bytes == 300
        assert plan.segments[1].container_offset == 300
        assert plan.segments[1].length_bytes == 200

    def test_plan_returns_correct_backup_set_id_and_source_root(self) -> None:
        files = [_make_file("f1", 10)]
        plan = self.planner.plan(files, self.config)
        assert plan.backup_set_id != ""
        assert PurePath(plan.source_root) == PurePath("/src")

    def test_segment_ids_use_file_id_and_sequence(self) -> None:
        # A 2000-byte file splits into 3 segments; ids should follow SEG-<file_id>-001 etc.
        files = [_make_file("myfile", 2000)]
        plan = self.planner.plan(files, self.config)
        assert plan.segments[0].segment_id == "SEG-myfile-001"
        assert plan.segments[1].segment_id == "SEG-myfile-002"
        assert plan.segments[2].segment_id == "SEG-myfile-003"

    def test_tape_ids_use_backup_set_id_and_sequence(self) -> None:
        files = [_make_file("f1", 2000)]
        plan = self.planner.plan(files, self.config)
        assert plan.tapes[0].tape_id == f"TAPE-{plan.backup_set_id}-001"
        assert plan.tapes[1].tape_id == f"TAPE-{plan.backup_set_id}-002"
        assert plan.tapes[2].tape_id == f"TAPE-{plan.backup_set_id}-003"

    def test_empty_source_files_returns_plan_with_no_tapes(self) -> None:
        plan = self.planner.plan([], self.config)
        assert plan.tapes == []
        assert plan.segments == []
        assert plan.source_files == []
