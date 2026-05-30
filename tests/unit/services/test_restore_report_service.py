"""Unit tests for RestoreReportService."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from lto_backup.domain.catalog import Catalog
from lto_backup.domain.container import Container
from lto_backup.domain.container_restore_result import ContainerRestoreResult
from lto_backup.domain.restore_report import RestoreReport
from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape import Tape
from lto_backup.domain.tape_segment import TapeSegment
from lto_backup.exceptions.file_write_error import FileWriteError
from lto_backup.services.restore_report_service import (
    DETAIL_CONTAINER,
    DETAIL_FILE,
    RestoreReportService,
)

_SET_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_TAPE_ID = f"TAPE-{_SET_ID}-001"
_TAPE_ID_2 = f"TAPE-{_SET_ID}-002"
_CNT_ID = f"CNT-{_SET_ID}-00001"
_CNT_ID_2 = f"CNT-{_SET_ID}-00002"
_CREATED_AT = datetime(2026, 5, 30, 10, 0, 0, tzinfo=timezone.utc)
_RESTORE_ROOT = Path("/out/restore")


def _make_tape(tape_id: str = _TAPE_ID, seq: int = 1) -> Tape:
    return Tape(
        tape_id=tape_id,
        backup_set_id=_SET_ID,
        sequence_number=seq,
        nominal_capacity_bytes=10_000_000_000,
        reserved_catalog_bytes=1_000,
    )


def _make_container(
    container_id: str = _CNT_ID,
    tape_id: str = _TAPE_ID,
    seq: int = 1,
) -> Container:
    return Container(
        container_id=container_id,
        backup_set_id=_SET_ID,
        tape_id=tape_id,
        sequence_number=seq,
        tape_offset=0,
        size_bytes=1_000_000,
        sha256="a" * 64,
    )


def _make_source_file(
    file_id: str = "f-001",
    relative_path: str = "docs/report.pdf",
    size_bytes: int = 512_000,
) -> SourceFile:
    return SourceFile(
        file_id=file_id,
        relative_path=relative_path,
        absolute_path=f"/src/{relative_path}",
        size_bytes=size_bytes,
        sha256="b" * 64,
        modified_at=_CREATED_AT,
    )


def _make_segment(
    segment_id: str = "seg-001",
    file_id: str = "f-001",
    container_id: str = _CNT_ID,
) -> TapeSegment:
    return TapeSegment(
        segment_id=segment_id,
        file_id=file_id,
        container_id=container_id,
        container_offset=0,
        source_offset=0,
        length_bytes=512_000,
        sha256="c" * 64,
    )


def _make_catalog(
    tapes: list[Tape] | None = None,
    containers: list[Container] | None = None,
    source_files: list[SourceFile] | None = None,
    segments: list[TapeSegment] | None = None,
) -> Catalog:
    return Catalog(
        schema_version="2.0",
        backup_set_id=_SET_ID,
        created_at=_CREATED_AT,
        source_root="/src",
        tapes=tapes or [_make_tape()],
        containers=containers or [_make_container()],
        source_files=source_files or [_make_source_file()],
        segments=segments or [_make_segment()],
    )


def _clean_report(
    files_requested: int = 1,
    files_restored: int = 1,
) -> RestoreReport:
    return RestoreReport(
        files_requested=files_requested,
        files_restored=files_restored,
    )


def _failing_report(relative_path: str, error_msg: str) -> RestoreReport:
    return RestoreReport(
        files_requested=1,
        files_restored=0,
        errors=[error_msg],
        failed_paths=[relative_path],
    )


class TestRestoreReportServiceGenerate:
    def test_creates_html_file_in_output_dir(self, tmp_path: Path) -> None:
        catalog = _make_catalog()

        result = RestoreReportService().generate(
            catalog, _clean_report(), _RESTORE_ROOT, None, tmp_path
        )

        assert result == tmp_path / f"restore-report-{_SET_ID}.html"
        assert result.exists()

    def test_creates_output_dir_if_missing(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "reports" / "nested"
        catalog = _make_catalog()

        RestoreReportService().generate(
            catalog, _clean_report(), _RESTORE_ROOT, None, output_dir
        )

        assert output_dir.is_dir()

    def test_raises_file_write_error_on_io_failure(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "not_a_dir"
        bad_path.write_text("occupied")
        catalog = _make_catalog()

        with pytest.raises(FileWriteError):
            RestoreReportService().generate(
                catalog, _clean_report(), _RESTORE_ROOT, None, bad_path
            )


class TestRestoreReportServiceContent:
    def setup_method(self) -> None:
        self._svc = RestoreReportService()

    def _html(
        self,
        catalog: Catalog,
        rr: RestoreReport | None = None,
        filter_glob: str | None = None,
        detail_level: str = DETAIL_CONTAINER,
    ) -> str:
        return self._svc._render(
            catalog, rr or _clean_report(), _RESTORE_ROOT, filter_glob, detail_level
        )

    def test_contains_backup_set_id(self) -> None:
        html = self._html(_make_catalog())

        assert _SET_ID in html

    def test_contains_restore_root(self) -> None:
        html = self._html(_make_catalog())

        assert str(_RESTORE_ROOT) in html

    def test_contains_source_root(self) -> None:
        html = self._html(_make_catalog())

        assert "/src" in html

    def test_verdict_pass_when_no_errors(self) -> None:
        html = self._html(_make_catalog(), _clean_report())

        assert ">PASS<" in html
        assert ">FAIL<" not in html

    def test_verdict_fail_when_errors_present(self) -> None:
        rr = _failing_report("docs/report.pdf", "Tape X: segment checksum mismatch")

        html = self._html(_make_catalog(), rr)

        assert ">FAIL<" in html
        assert ">PASS<" not in html

    def test_error_message_appears_in_html(self) -> None:
        msg = "Tape TAPE-001: segment seg-001 checksum mismatch"
        rr = _failing_report("docs/report.pdf", msg)

        html = self._html(_make_catalog(), rr)

        assert msg in html

    def test_file_path_appears_in_table(self) -> None:
        html = self._html(_make_catalog(), detail_level=DETAIL_FILE)

        assert "docs/report.pdf" in html

    def test_restored_file_shows_pass_marker(self) -> None:
        html = self._html(_make_catalog(), _clean_report(), detail_level=DETAIL_FILE)

        assert "Restored" in html

    def test_failed_file_shows_fail_marker(self) -> None:
        rr = _failing_report("docs/report.pdf", "some error")

        html = self._html(_make_catalog(), rr, detail_level=DETAIL_FILE)

        assert "Failed" in html

    def test_filter_glob_displayed(self) -> None:
        html = self._html(_make_catalog(), filter_glob="docs/*.pdf")

        assert "docs/*.pdf" in html

    def test_no_filter_shows_all_files_message(self) -> None:
        html = self._html(_make_catalog(), filter_glob=None)

        assert "all files" in html

    def test_tape_id_appears_in_tapes_section(self) -> None:
        html = self._html(_make_catalog())

        assert _TAPE_ID in html

    def test_only_relevant_tape_shown_for_filter(self) -> None:
        """A tape with no segments for the filtered file must not appear."""
        sf1 = _make_source_file("f-001", "a/file.txt")
        sf2 = _make_source_file("f-002", "b/other.txt")
        tape1 = _make_tape(_TAPE_ID, seq=1)
        tape2 = _make_tape(_TAPE_ID_2, seq=2)
        cnt1 = _make_container(_CNT_ID, _TAPE_ID)
        cnt2 = _make_container(_CNT_ID_2, _TAPE_ID_2, seq=2)
        seg1 = _make_segment("seg-001", "f-001", _CNT_ID)
        seg2 = _make_segment("seg-002", "f-002", _CNT_ID_2)
        catalog = _make_catalog(
            tapes=[tape1, tape2],
            containers=[cnt1, cnt2],
            source_files=[sf1, sf2],
            segments=[seg1, seg2],
        )

        html = self._html(catalog, filter_glob="a/*")

        assert _TAPE_ID in html
        assert _TAPE_ID_2 not in html

    def test_files_requested_count_in_summary(self) -> None:
        html = self._html(_make_catalog(), _clean_report(files_requested=5, files_restored=5))

        assert "5" in html

    def test_no_errors_section_when_clean(self) -> None:
        html = self._html(_make_catalog(), _clean_report())

        assert "<h2>Errors</h2>" not in html

    def test_errors_section_present_when_failed(self) -> None:
        rr = _failing_report("docs/report.pdf", "segment mismatch")

        html = self._html(_make_catalog(), rr)

        assert "<h2>Errors</h2>" in html

    def test_filter_excludes_non_matching_files(self) -> None:
        sf1 = _make_source_file("f-001", "docs/keep.pdf")
        sf2 = _make_source_file("f-002", "other/skip.txt")
        catalog = _make_catalog(source_files=[sf1, sf2])

        html = self._html(catalog, filter_glob="docs/*.pdf", detail_level=DETAIL_FILE)

        assert "docs/keep.pdf" in html
        assert "other/skip.txt" not in html


def _make_container_result(
    container_id: str = _CNT_ID,
    tape_id: str = _TAPE_ID,
    sha256_passed: bool = True,
    error: str | None = None,
) -> ContainerRestoreResult:
    return ContainerRestoreResult(
        container_id=container_id,
        tape_id=tape_id,
        sha256_passed=sha256_passed,
        error=error,
    )


class TestRestoreReportDetailLevelContainer:
    """Container-level detail section tests."""

    def setup_method(self) -> None:
        self._svc = RestoreReportService()

    def _html(
        self,
        catalog: Catalog,
        rr: RestoreReport | None = None,
    ) -> str:
        return self._svc._render(
            catalog, rr or _clean_report(), _RESTORE_ROOT, None, DETAIL_CONTAINER
        )

    def test_container_section_heading_present(self) -> None:
        rr = RestoreReport(
            files_requested=1,
            files_restored=1,
            container_results=[_make_container_result()],
        )

        html = self._html(_make_catalog(), rr)

        assert "Container Verification" in html

    def test_container_id_appears_in_table(self) -> None:
        rr = RestoreReport(
            files_requested=1,
            files_restored=1,
            container_results=[_make_container_result(container_id=_CNT_ID)],
        )

        html = self._html(_make_catalog(), rr)

        assert _CNT_ID in html

    def test_passed_container_shows_pass_marker(self) -> None:
        rr = RestoreReport(
            files_requested=1,
            files_restored=1,
            container_results=[_make_container_result(sha256_passed=True)],
        )

        html = self._html(_make_catalog(), rr)

        assert "Pass" in html

    def test_failed_container_shows_fail_marker(self) -> None:
        rr = RestoreReport(
            files_requested=1,
            files_restored=0,
            errors=["CNT-001: SHA-256 mismatch"],
            container_results=[
                _make_container_result(
                    sha256_passed=False, error="CNT-001: SHA-256 mismatch"
                )
            ],
        )

        html = self._html(_make_catalog(), rr)

        assert "Fail" in html

    def test_failed_container_error_message_in_table(self) -> None:
        msg = "CNT-001: SHA-256 mismatch (expected aaa, got bbb)"
        rr = RestoreReport(
            files_requested=1,
            files_restored=0,
            errors=[msg],
            container_results=[_make_container_result(sha256_passed=False, error=msg)],
        )

        html = self._html(_make_catalog(), rr)

        assert msg in html

    def test_file_status_table_not_present_in_container_mode(self) -> None:
        html = self._html(_make_catalog())

        assert "<h2>File Status</h2>" not in html

    def test_no_container_results_hides_container_section(self) -> None:
        html = self._html(_make_catalog(), _clean_report())

        assert "Container Verification" not in html

    def test_summary_shows_containers_verified_count(self) -> None:
        rr = RestoreReport(
            files_requested=1,
            files_restored=1,
            container_results=[_make_container_result()],
        )

        html = self._html(_make_catalog(), rr)

        assert "Containers Verified" in html


class TestRestoreReportDetailLevelFile:
    """File-level detail section tests."""

    def setup_method(self) -> None:
        self._svc = RestoreReportService()

    def _html(
        self,
        catalog: Catalog,
        rr: RestoreReport | None = None,
    ) -> str:
        return self._svc._render(
            catalog, rr or _clean_report(), _RESTORE_ROOT, None, DETAIL_FILE
        )

    def test_file_status_heading_present(self) -> None:
        html = self._html(_make_catalog())

        assert "File Status" in html

    def test_file_path_in_table(self) -> None:
        html = self._html(_make_catalog())

        assert "docs/report.pdf" in html

    def test_container_section_not_present_in_file_mode(self) -> None:
        html = self._html(_make_catalog())

        assert "Container Verification" not in html

    def test_restored_file_shows_tick(self) -> None:
        html = self._html(_make_catalog(), _clean_report())

        assert "Restored" in html

    def test_failed_file_shows_cross(self) -> None:
        rr = _failing_report("docs/report.pdf", "segment error")

        html = self._html(_make_catalog(), rr)

        assert "Failed" in html


class TestRestoreReportDetailLevelGenerateFlag:
    """generate() passes detail_level through to HTML."""

    def test_generate_container_detail(self, tmp_path: Path) -> None:
        catalog = _make_catalog()
        rr = RestoreReport(
            files_requested=1,
            files_restored=1,
            container_results=[_make_container_result()],
        )

        path = RestoreReportService().generate(
            catalog, rr, _RESTORE_ROOT, None, tmp_path, DETAIL_CONTAINER
        )

        assert "Container Verification" in path.read_text(encoding="utf-8")

    def test_generate_file_detail(self, tmp_path: Path) -> None:
        catalog = _make_catalog()

        path = RestoreReportService().generate(
            catalog, _clean_report(), _RESTORE_ROOT, None, tmp_path, DETAIL_FILE
        )

        assert "File Status" in path.read_text(encoding="utf-8")
