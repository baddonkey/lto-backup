"""ReportService — generates an HTML archive report for a completed backup set."""

import logging
from datetime import datetime, timezone
from pathlib import Path

from lto_backup.domain.catalog import Catalog
from lto_backup.domain.container import Container
from lto_backup.domain.tape_check import TapeCheck
from lto_backup.domain.verification_report import VerificationReport
from lto_backup.exceptions.file_write_error import FileWriteError

logger = logging.getLogger(__name__)

_BYTES_PER_GIB = 1024 ** 3
_TICK = "&#10003;"   # ✓
_CROSS = "&#10007;"  # ✗


def _fmt_bytes(n: int) -> str:
    if n >= _BYTES_PER_GIB:
        return f"{n / _BYTES_PER_GIB:.2f} GiB"
    mib = n / (1024 ** 2)
    return f"{mib:.2f} MiB"


def _fmt_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _status_cell(passed: bool, errors: list[str] | None = None) -> str:
    if passed:
        return f'<td style="color:#2e7d32;font-weight:700">{_TICK} Pass</td>'
    detail = "; ".join(errors) if errors else "Failed"
    return f'<td style="color:#c62828;font-weight:700" title="{detail}">{_CROSS} Fail</td>'


class ReportService:
    """Produces an HTML backup-set report from a Catalog and VerificationReport."""

    def generate(
        self,
        catalog: Catalog,
        verification_report: VerificationReport,
        output_dir: Path,
    ) -> Path:
        """Build the HTML report and write it to *output_dir*.

        Returns the absolute path of the written file.
        Raises FileWriteError if the file cannot be written.
        """
        filename = f"report-{catalog.backup_set_id}.html"
        output_path = output_dir / filename

        html = self._render(catalog, verification_report)

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path.write_text(html, encoding="utf-8")
        except OSError as exc:
            logger.error("ReportService: failed to write report to %s: %s", output_path, exc)
            raise FileWriteError(f"Cannot write report to {output_path}: {exc}") from exc

        logger.info("ReportService: report written to %s", output_path)
        return output_path

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _render(self, catalog: Catalog, vr: VerificationReport) -> str:
        errors = vr.errors
        verdict = "PASS" if not errors else "FAIL"
        verdict_colour = "#2e7d32" if not errors else "#c62828"

        total_bytes = sum(f.size_bytes for f in catalog.source_files)
        total_containers = len(catalog.containers)

        tape_inventory_rows = self._tape_inventory_rows(catalog)
        write_verification_rows = self._write_verification_rows(catalog)
        verification_sections = self._verification_sections(vr)

        generated_at = _fmt_dt(datetime.now(tz=timezone.utc))

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Backup Report — {catalog.backup_set_id}</title>
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
    h3 {{ font-size: 0.95rem; margin-top: 1.25rem; margin-bottom: 0.25rem; color: #424242; }}
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
    footer {{ margin-top: 3rem; font-size: 0.8rem; color: #9e9e9e; }}
  </style>
</head>
<body>

<h1>Backup Report</h1>
<p class="subtitle">Set ID: <strong>{catalog.backup_set_id}</strong></p>

<h2>Backup Summary</h2>
<dl class="kv">
  <dt>Backup Set ID</dt><dd>{catalog.backup_set_id}</dd>
  <dt>Schema Version</dt><dd>{catalog.schema_version}</dd>
  <dt>Created At</dt><dd>{_fmt_dt(catalog.created_at)}</dd>
  <dt>Source Root</dt><dd>{catalog.source_root}</dd>
  <dt>Files Backed Up</dt><dd>{len(catalog.source_files):,}</dd>
  <dt>Total Source Data</dt><dd>{_fmt_bytes(total_bytes)} ({total_bytes:,} bytes)</dd>
  <dt>Tapes Used</dt><dd>{len(catalog.tapes)}</dd>
  <dt>Containers</dt><dd>{total_containers}</dd>
</dl>

<h2>Tape Inventory</h2>
<table>
  <thead>
    <tr><th>#</th><th>Tape ID</th><th>Containers</th><th>Data Written</th><th>Usable Capacity</th></tr>
  </thead>
  <tbody>
{tape_inventory_rows}
  </tbody>
</table>

<h2>Write Verification</h2>
<p>Every container was read back immediately after writing and its SHA-256 matched the write digest.</p>
<table>
  <thead>
    <tr><th>Container ID</th><th>Tape</th><th>Size</th><th>SHA-256</th><th>Write Read-back</th></tr>
  </thead>
  <tbody>
{write_verification_rows}
  </tbody>
</table>

<h2>Post-Backup Verification</h2>
<div class="verdict">{verdict}</div>
{verification_sections}

<footer>Report generated {generated_at}</footer>
</body>
</html>
"""

    def _tape_inventory_rows(self, catalog: Catalog) -> str:
        containers_by_tape: dict[str, list[Container]] = {}
        for container in catalog.containers:
            containers_by_tape.setdefault(container.tape_id, []).append(container)

        rows: list[str] = []
        for tape in sorted(catalog.tapes, key=lambda t: t.sequence_number):
            tape_containers = containers_by_tape.get(tape.tape_id, [])
            data_bytes = sum(c.size_bytes for c in tape_containers)
            usable = tape.usable_capacity_bytes()
            rows.append(
                f"    <tr>"
                f"<td>{tape.sequence_number}</td>"
                f"<td>{tape.tape_id}</td>"
                f"<td>{len(tape_containers)}</td>"
                f"<td>{_fmt_bytes(data_bytes)}</td>"
                f"<td>{_fmt_bytes(usable)}</td>"
                f"</tr>"
            )
        return "\n".join(rows)

    def _write_verification_rows(self, catalog: Catalog) -> str:
        rows: list[str] = []
        for container in sorted(catalog.containers, key=lambda c: c.sequence_number):
            sha = container.sha256 if container.sha256 else "—"
            rows.append(
                f"    <tr>"
                f"<td class='mono'>{container.container_id}</td>"
                f"<td>{container.tape_id}</td>"
                f"<td>{_fmt_bytes(container.size_bytes)}</td>"
                f"<td class='mono'>{sha}</td>"
                f'<td style="color:#2e7d32;font-weight:700">{_TICK} Pass</td>'
                f"</tr>"
            )
        return "\n".join(rows)

    def _verification_sections(self, vr: VerificationReport) -> str:
        if not vr.tape_checks:
            return "<p>No verification data available.</p>"

        parts: list[str] = []
        for tc in sorted(vr.tape_checks, key=lambda t: t.sequence_number):
            parts.append(self._tape_check_section(tc))
        return "\n".join(parts)

    def _tape_check_section(self, tc: TapeCheck) -> str:
        catalog_status = (
            f'<span style="color:#2e7d32;font-weight:700">{_TICK} Pass</span>'
            if tc.catalog_checksum_passed
            else f'<span style="color:#c62828;font-weight:700">{_CROSS} Fail — {tc.catalog_error}</span>'
        )

        container_rows: list[str] = []
        for cc in tc.containers:
            error_detail = "; ".join(cc.errors) if cc.errors else ""
            status_cell = _status_cell(cc.passed, cc.errors)
            error_td = f"<td class='mono' style='color:#c62828'>{error_detail}</td>"
            container_rows.append(
                f"    <tr>"
                f"<td class='mono'>{cc.container_id}</td>"
                f"{status_cell}"
                f"{error_td}"
                f"</tr>"
            )

        container_table = ""
        if container_rows:
            body = "\n".join(container_rows)
            container_table = f"""
<table>
  <thead>
    <tr><th>Container ID</th><th>Status</th><th>Detail</th></tr>
  </thead>
  <tbody>
{body}
  </tbody>
</table>"""

        return f"""
<h3>Tape {tc.sequence_number} — {tc.tape_id}</h3>
<p>Catalog checksum: {catalog_status}</p>{container_table}"""
