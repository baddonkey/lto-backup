"""RestoreReportService — generates an HTML restore report."""

import fnmatch
import logging
from datetime import datetime, timezone
from pathlib import Path

from lto_backup.domain.catalog import Catalog
from lto_backup.domain.container import Container
from lto_backup.domain.container_restore_result import ContainerRestoreResult
from lto_backup.domain.restore_report import RestoreReport
from lto_backup.domain.source_file import SourceFile
from lto_backup.exceptions.file_write_error import FileWriteError

logger = logging.getLogger(__name__)

_BYTES_PER_GIB = 1024 ** 3
_TICK = "&#10003;"   # ✓
_CROSS = "&#10007;"  # ✗

DETAIL_CONTAINER = "container"
DETAIL_FILE = "file"


def _fmt_bytes(n: int) -> str:
    if n >= _BYTES_PER_GIB:
        return f"{n / _BYTES_PER_GIB:.2f} GiB"
    mib = n / (1024 ** 2)
    return f"{mib:.2f} MiB"


def _fmt_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


class RestoreReportService:
    """Produces an HTML restore report from a Catalog and RestoreReport.

    detail_level controls the detail table shown:
      "container" (default) — per-container SHA-256 verification status.
      "file"                — per-file restore status.
    """

    def generate(
        self,
        catalog: Catalog,
        restore_report: RestoreReport,
        restore_root: Path,
        filter_glob: str | None,
        output_dir: Path,
        detail_level: str = DETAIL_CONTAINER,
    ) -> Path:
        """Build the HTML report and write it to *output_dir*.

        Returns the absolute path of the written file.
        Raises FileWriteError if the file cannot be written.
        """
        filename = f"restore-report-{catalog.backup_set_id}.html"
        output_path = output_dir / filename

        html = self._render(
            catalog, restore_report, restore_root, filter_glob, detail_level
        )

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path.write_text(html, encoding="utf-8")
        except OSError as exc:
            logger.error(
                "RestoreReportService: failed to write report to %s: %s",
                output_path,
                exc,
            )
            raise FileWriteError(
                f"Cannot write restore report to {output_path}: {exc}"
            ) from exc

        logger.info("RestoreReportService: report written to %s", output_path)
        return output_path

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _render(
        self,
        catalog: Catalog,
        rr: RestoreReport,
        restore_root: Path,
        filter_glob: str | None,
        detail_level: str,
    ) -> str:
        verdict = "PASS" if not rr.errors else "FAIL"
        verdict_colour = "#2e7d32" if not rr.errors else "#c62828"
        files_failed = rr.files_requested - rr.files_restored
        containers_failed = sum(
            1 for cr in rr.container_results if not cr.sha256_passed
        )

        requested_files = self._requested_files(catalog, filter_glob)
        tapes_rows = self._tapes_rows(catalog, requested_files)

        if detail_level == DETAIL_FILE:
            detail_section = self._file_detail_section(requested_files, rr)
        else:
            detail_section = self._container_detail_section(catalog, rr)

        error_section = self._error_section(rr)
        generated_at = _fmt_dt(datetime.now(tz=timezone.utc))
        filter_display = filter_glob if filter_glob else "— (all files)"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Restore Report — {catalog.backup_set_id}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      color: #212121;
      margin: 0;
      padding: 2rem 3rem;
      background: #fff;
    }}
    h1 {{ font-size: 1.6rem; margin-bottom: 0.25rem; }}
    h2 {{ font-size: 1.1rem; margin-top: 2rem; border-bottom: 1px solid #e0e0e0; padding-bottom: 0.3rem; }}
    .subtitle {{ color: #757575; margin-bottom: 2rem; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 0.5rem; }}
    th {{ background: #f5f5f5; text-align: left; padding: 6px 10px; font-weight: 600; border: 1px solid #e0e0e0; }}
    td {{ padding: 5px 10px; border: 1px solid #e0e0e0; font-variant-numeric: tabular-nums; }}
    tr:nth-child(even) td {{ background: #fafafa; }}
    .mono {{ font-family: monospace; font-size: 0.82rem; }}
    .kv {{ display: grid; grid-template-columns: 200px 1fr; row-gap: 4px; margin-top: 0.75rem; }}
    .kv dt {{ font-weight: 600; color: #424242; }}
    .kv dd {{ margin: 0; }}
    .verdict {{
      display: inline-block;
      font-size: 1.1rem;
      font-weight: 700;
      padding: 4px 14px;
      border-radius: 4px;
      color: #fff;
      background: {verdict_colour};
      margin-top: 0.75rem;
      margin-bottom: 0.75rem;
    }}
    .err-list {{ margin: 0; padding-left: 1.2rem; }}
    .err-list li {{ margin-bottom: 0.2rem; color: #c62828; font-family: monospace; font-size: 0.82rem; }}
    footer {{ margin-top: 3rem; font-size: 0.8rem; color: #9e9e9e; }}
  </style>
</head>
<body>

<h1>Restore Report</h1>
<p class="subtitle">Set ID: <strong>{catalog.backup_set_id}</strong></p>

<h2>Restore Summary</h2>
<div class="verdict">{verdict}</div>
<dl class="kv">
  <dt>Backup Set ID</dt><dd>{catalog.backup_set_id}</dd>
  <dt>Backup Created At</dt><dd>{_fmt_dt(catalog.created_at)}</dd>
  <dt>Source Root</dt><dd>{catalog.source_root}</dd>
  <dt>Restore Root</dt><dd>{restore_root}</dd>
  <dt>Filter</dt><dd>{filter_display}</dd>
  <dt>Detail Level</dt><dd>{detail_level}</dd>
  <dt>Files Requested</dt><dd>{rr.files_requested:,}</dd>
  <dt>Files Restored</dt><dd><span style="color:#2e7d32;font-weight:700">{rr.files_restored:,}</span></dd>
  <dt>Files Failed</dt><dd><span style="color:{'#c62828' if files_failed else '#2e7d32'};font-weight:700">{files_failed:,}</span></dd>
  <dt>Containers Verified</dt><dd>{len(rr.container_results):,}</dd>
  <dt>Containers Failed</dt><dd><span style="color:{'#c62828' if containers_failed else '#2e7d32'};font-weight:700">{containers_failed:,}</span></dd>
  <dt>Errors</dt><dd>{len(rr.errors):,}</dd>
</dl>

<h2>Tapes Accessed</h2>
<table>
  <thead>
    <tr><th>#</th><th>Tape ID</th><th>Containers</th></tr>
  </thead>
  <tbody>
{tapes_rows}
  </tbody>
</table>

{detail_section}
{error_section}
<footer>Report generated {generated_at}</footer>
</body>
</html>
"""

    def _requested_files(
        self,
        catalog: Catalog,
        filter_glob: str | None,
    ) -> list[SourceFile]:
        if filter_glob is None:
            return list(catalog.source_files)
        return [
            sf
            for sf in catalog.source_files
            if fnmatch.fnmatch(sf.relative_path, filter_glob)
        ]

    def _tapes_rows(
        self,
        catalog: Catalog,
        requested_files: list[SourceFile],
    ) -> str:
        file_ids = {sf.file_id for sf in requested_files}
        relevant_container_ids = {
            seg.container_id
            for seg in catalog.segments
            if seg.file_id in file_ids
        }
        relevant_tape_ids = {
            c.tape_id
            for c in catalog.containers
            if c.container_id in relevant_container_ids
        }
        containers_per_tape: dict[str, int] = {}
        for c in catalog.containers:
            if c.tape_id in relevant_tape_ids:
                containers_per_tape[c.tape_id] = (
                    containers_per_tape.get(c.tape_id, 0) + 1
                )

        rows: list[str] = []
        for tape in sorted(catalog.tapes, key=lambda t: t.sequence_number):
            if tape.tape_id not in relevant_tape_ids:
                continue
            rows.append(
                f"    <tr>"
                f"<td>{tape.sequence_number}</td>"
                f"<td>{tape.tape_id}</td>"
                f"<td>{containers_per_tape.get(tape.tape_id, 0)}</td>"
                f"</tr>"
            )
        return "\n".join(rows)

    def _container_detail_section(
        self,
        catalog: Catalog,
        rr: RestoreReport,
    ) -> str:
        if not rr.container_results:
            return ""

        container_by_id: dict[str, Container] = {
            c.container_id: c for c in catalog.containers
        }
        result_by_id: dict[str, ContainerRestoreResult] = {
            cr.container_id: cr for cr in rr.container_results
        }

        rows: list[str] = []
        for container in sorted(
            catalog.containers, key=lambda c: (c.tape_id, c.tape_offset)
        ):
            cr = result_by_id.get(container.container_id)
            if cr is None:
                continue
            cont = container_by_id[container.container_id]
            if cr.sha256_passed:
                status_td = (
                    f'<td style="color:#2e7d32;font-weight:700">{_TICK} Pass</td>'
                )
                error_td = "<td></td>"
            else:
                status_td = (
                    f'<td style="color:#c62828;font-weight:700">{_CROSS} Fail</td>'
                )
                error_td = (
                    f"<td class='mono' style='color:#c62828'>"
                    f"{cr.error or ''}</td>"
                )
            rows.append(
                f"    <tr>"
                f"<td class='mono'>{cont.container_id}</td>"
                f"<td>{cr.tape_id}</td>"
                f"<td>{_fmt_bytes(cont.size_bytes)}</td>"
                f"{status_td}"
                f"{error_td}"
                f"</tr>"
            )

        body = "\n".join(rows)
        return f"""<h2>Container Verification</h2>
<table>
  <thead>
    <tr><th>Container ID</th><th>Tape</th><th>Size</th><th>SHA-256</th><th>Detail</th></tr>
  </thead>
  <tbody>
{body}
  </tbody>
</table>
"""

    def _file_detail_section(
        self,
        requested_files: list[SourceFile],
        rr: RestoreReport,
    ) -> str:
        failed_set = set(rr.failed_paths)
        rows: list[str] = []
        for sf in sorted(requested_files, key=lambda f: f.relative_path):
            failed = sf.relative_path in failed_set
            if failed:
                status_td = (
                    f'<td style="color:#c62828;font-weight:700">'
                    f"{_CROSS} Failed</td>"
                )
            else:
                status_td = (
                    f'<td style="color:#2e7d32;font-weight:700">'
                    f"{_TICK} Restored</td>"
                )
            rows.append(
                f"    <tr>"
                f"<td class='mono'>{sf.relative_path}</td>"
                f"<td>{_fmt_bytes(sf.size_bytes)}</td>"
                f"{status_td}"
                f"</tr>"
            )
        body = "\n".join(rows)
        return f"""<h2>File Status</h2>
<table>
  <thead>
    <tr><th>Path</th><th>Size</th><th>Status</th></tr>
  </thead>
  <tbody>
{body}
  </tbody>
</table>
"""

    def _error_section(self, rr: RestoreReport) -> str:
        if not rr.errors:
            return ""
        items = "\n".join(
            f"    <li>{error}</li>" for error in rr.errors
        )
        return f"""
<h2>Errors</h2>
<ul class="err-list">
{items}
</ul>
"""
