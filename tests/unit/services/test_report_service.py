"""Unit tests for ReportService."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from lto_backup.domain.catalog import Catalog
from lto_backup.domain.container import Container
from lto_backup.domain.container_check import ContainerCheck
from lto_backup.domain.source_file import SourceFile
from lto_backup.domain.tape import Tape
from lto_backup.domain.tape_check import TapeCheck
from lto_backup.domain.verification_report import VerificationReport
from lto_backup.exceptions.file_write_error import FileWriteError
from lto_backup.services.report_service import ReportService

_SET_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_TAPE_ID = f"TAPE-{_SET_ID}-001"
_CONTAINER_ID = f"CNT-{_SET_ID}-00001"
_CREATED_AT = datetime(2026, 5, 30, 10, 0, 0, tzinfo=timezone.utc)


def _make_tape(tape_id: str = _TAPE_ID, seq: int = 1) -> Tape:
    return Tape(
        tape_id=tape_id,
        backup_set_id=_SET_ID,
        sequence_number=seq,
        nominal_capacity_bytes=10_000_000_000,
        reserved_catalog_bytes=1_000,
    )


def _make_container(container_id: str = _CONTAINER_ID, tape_id: str = _TAPE_ID) -> Container:
    return Container(
        container_id=container_id,
        backup_set_id=_SET_ID,
        tape_id=tape_id,
        sequence_number=1,
        tape_offset=0,
        size_bytes=500_000_000,
        sha256="a" * 64,
    )


def _make_source_file() -> SourceFile:
    return SourceFile(
        file_id="file-001",
        relative_path="docs/file.pdf",
        absolute_path="/home/user/docs/file.pdf",
        size_bytes=500_000_000,
        sha256="b" * 64,
        modified_at=_CREATED_AT,
    )


def _make_catalog(
    tapes: list[Tape] | None = None,
    containers: list[Container] | None = None,
    source_files: list[SourceFile] | None = None,
) -> Catalog:
    return Catalog(
        schema_version="2.0",
        backup_set_id=_SET_ID,
        created_at=_CREATED_AT,
        source_root="/home/user/docs",
        tapes=tapes or [_make_tape()],
        containers=containers or [_make_container()],
        source_files=source_files or [_make_source_file()],
    )


def _clean_vr(tape_id: str = _TAPE_ID, seq: int = 1) -> VerificationReport:
    return VerificationReport(tape_checks=[
        TapeCheck(
            tape_id=tape_id,
            sequence_number=seq,
            catalog_checksum_passed=True,
            catalog_error=None,
            containers=[ContainerCheck(container_id=_CONTAINER_ID, passed=True)],
        )
    ])


def _failing_vr(error_msg: str) -> VerificationReport:
    return VerificationReport(tape_checks=[
        TapeCheck(
            tape_id=_TAPE_ID,
            sequence_number=1,
            catalog_checksum_passed=False,
            catalog_error=error_msg,
            containers=[],
        )
    ])


class TestReportServiceGenerate:
    def test_creates_html_file_in_output_dir(self, tmp_path: Path) -> None:
        catalog = _make_catalog()
        svc = ReportService()

        result = svc.generate(catalog, _clean_vr(), tmp_path)

        assert result == tmp_path / f"report-{_SET_ID}.html"
        assert result.exists()

    def test_creates_output_dir_if_missing(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "reports" / "nested"
        catalog = _make_catalog()

        ReportService().generate(catalog, _clean_vr(), output_dir)

        assert output_dir.is_dir()

    def test_raises_file_write_error_on_io_failure(self, tmp_path: Path) -> None:
        # Make output_dir a file so mkdir/write fails
        bad_path = tmp_path / "not_a_dir"
        bad_path.write_text("occupied")
        catalog = _make_catalog()

        with pytest.raises(FileWriteError):
            ReportService().generate(catalog, _clean_vr(), bad_path)


class TestReportServiceContent:
    def setup_method(self) -> None:
        self._svc = ReportService()

    def _html(self, catalog: Catalog, vr: VerificationReport | None = None) -> str:
        return self._svc._render(catalog, vr or _clean_vr())

    def test_contains_backup_set_id(self) -> None:
        html = self._html(_make_catalog())

        assert _SET_ID in html

    def test_contains_tape_id(self) -> None:
        html = self._html(_make_catalog())

        assert _TAPE_ID in html

    def test_contains_source_root(self) -> None:
        html = self._html(_make_catalog())

        assert "/home/user/docs" in html

    def test_verdict_pass_when_no_errors(self) -> None:
        html = self._html(_make_catalog(), _clean_vr())

        assert ">PASS<" in html
        assert ">FAIL<" not in html

    def test_verdict_fail_when_errors_present(self) -> None:
        html = self._html(_make_catalog(), _failing_vr("Tape X: checksum mismatch"))

        assert ">FAIL<" in html
        assert ">PASS<" not in html

    def test_error_message_appears_in_html(self) -> None:
        error_msg = "Tape TAPE-001: catalog checksum mismatch"
        html = self._html(_make_catalog(), _failing_vr(error_msg))

        assert error_msg in html

    def test_file_count_in_summary(self) -> None:
        source_files = [_make_source_file(), _make_source_file()]
        catalog = _make_catalog(source_files=source_files)

        html = self._html(catalog)

        assert "2" in html

    def test_multiple_tapes_all_appear(self) -> None:
        tape1 = _make_tape(f"TAPE-{_SET_ID}-001", seq=1)
        tape2 = _make_tape(f"TAPE-{_SET_ID}-002", seq=2)
        cnt1 = _make_container(f"CNT-{_SET_ID}-00001", tape_id=tape1.tape_id)
        cnt2 = _make_container(f"CNT-{_SET_ID}-00002", tape_id=tape2.tape_id)
        catalog = _make_catalog(
            tapes=[tape1, tape2],
            containers=[cnt1, cnt2],
        )
        vr = VerificationReport(tape_checks=[
            TapeCheck(tape_id=tape1.tape_id, sequence_number=1,
                      catalog_checksum_passed=True, catalog_error=None),
            TapeCheck(tape_id=tape2.tape_id, sequence_number=2,
                      catalog_checksum_passed=True, catalog_error=None),
        ])

        html = self._html(catalog, vr)

        assert tape1.tape_id in html
        assert tape2.tape_id in html

    def test_no_errors_message_not_shown_with_detailed_sections(self) -> None:
        html = self._html(_make_catalog(), _clean_vr())

        assert "no errors detected" not in html

    def test_created_at_formatted(self) -> None:
        html = self._html(_make_catalog())

        assert "2026-05-30" in html

    def test_empty_catalog_renders_without_error(self) -> None:
        catalog = _make_catalog(tapes=[], containers=[], source_files=[])

        html = self._html(catalog, VerificationReport())

        assert _SET_ID in html

    def test_container_id_appears_in_write_section(self) -> None:
        html = self._html(_make_catalog())

        assert _CONTAINER_ID in html

    def test_tape_check_section_appears_per_tape(self) -> None:
        html = self._html(_make_catalog(), _clean_vr())

        assert _TAPE_ID in html

    def test_container_pass_shown_in_verification_section(self) -> None:
        html = self._html(_make_catalog(), _clean_vr())

        assert "Pass" in html

    def test_container_fail_shown_in_verification_section(self) -> None:
        vr = VerificationReport(tape_checks=[
            TapeCheck(
                tape_id=_TAPE_ID,
                sequence_number=1,
                catalog_checksum_passed=True,
                catalog_error=None,
                containers=[ContainerCheck(
                    container_id=_CONTAINER_ID,
                    passed=False,
                    errors=["segment mismatch"],
                )],
            )
        ])
        html = self._html(_make_catalog(), vr)

        assert "Fail" in html
        assert "segment mismatch" in html

        assert _SET_ID in html
