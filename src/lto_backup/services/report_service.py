"""ReportService — generates an HTML archive report for a completed backup set."""

import logging
from datetime import datetime, timezone
from pathlib import Path

from lto_backup.domain.catalog import Catalog
from lto_backup.domain.container import Container
from lto_backup.exceptions.file_write_error import FileWriteError

logger = logging.getLogger(__name__)

_BYTES_PER_GIB = 1024 ** 3


def _fmt_bytes(n: int) -> str:
    if n >= _BYTES_PER_GIB:
        return f"{n / _BYTES_PER_GIB:.2f} GiB"
    mib = n / (1024 ** 2)
    return f"{mib:.2f} MiB"


def _fmt_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


class ReportService:
    """Produces an HTML backup-set report from a Catalog and verification results."""

    def generate(
        self,
        catalog: Catalog,
        verification_errors: list[str],
        output_dir: Path,
    ) -> Path:
        """Build the HTML report and write it to *output_dir*.

        Returns the absolute path of the written file.
        Raises FileWriteError if the file cannot be written.
        """
        filename = f"report-{catalog.backup_set_id}.html"
        output_path = output_dir / filename

        html = self._render(catalog, verification_errors)

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

    def _render(self, catalog: Catalog, errors: list[str]) -> str:
        verdict = "PASS" if not errors else "FAIL"
        verdict_colour = "#2e7d32" if not errors else "#c62828"

        total_bytes = sum(f.size_bytes for f in catalog.source_files)
        total_containers = len(catalog.containers)

        tape_rows = self._tape_rows(catalog)
        error_section = self._error_section(errors)

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
    .subtitle {{ color: #757575; margin-bottom: 2rem; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 0.75rem; }}
    th {{ background: #f5f5f5; text-align: left; padding: 6px 10px; font-weight: 600; border: 1px solid #e0e0e0; }}
    td {{ padding: 5px 10px; border: 1px solid #e0e0e0; font-variant-numeric: tabular-nums; }}
    tr:nth-child(even) td {{ background: #fafafa; }}
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
    }}
    .errors {{ margin-top: 0.75rem; }}
    .errors li {{ font-family: monospace; font-size: 0.85rem; color: #c62828; margin-bottom: 4px; }}
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
    <tr>
      <th>#</th>
      <th>Tape ID</th>
      <th>Containers</th>
      <th>Data Written</th>
      <th>Usable Capacity</th>
    </tr>
  </thead>
  <tbody>
{tape_rows}
  </tbody>
</table>

<h2>Verification</h2>
<div class="verdict">{verdict}</div>
{error_section}

<footer>Report generated {generated_at}</footer>
</body>
</html>
"""

    def _tape_rows(self, catalog: Catalog) -> str:
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

    def _error_section(self, errors: list[str]) -> str:
        if not errors:
            return "<p>All checksums verified — no errors detected.</p>"
        items = "\n".join(f"    <li>{err}</li>" for err in errors)
        return f'<ul class="errors">\n{items}\n</ul>'
