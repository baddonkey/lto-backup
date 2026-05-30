# lto-backup

A Python 3.12+ application that backs up a file-based records management system to LTO tapes.

## Features

- Scan a source directory and produce a deterministic backup plan.
- Pack source files into fixed-size containers written as single blobs onto tape.
- Distribute containers across multiple tapes automatically.
- Split files that are larger than one container across container and tape boundaries.
- Store a full JSON catalog on every tape in the backup set for self-contained recovery.
- Verify tape contents against the catalog checksums after backup.
- **Restore** — reassemble any or all files from tape back to disk, with per-segment and full-file SHA-256 verification.
- Preserve original file timestamps (`mtime`) and Unix permission bits (`unix_mode`) on restore.
- Simulate a tape drive on disk for development and testing — no hardware required.
- Real LTO hardware support via LTFS on Linux.
- Pluggable design: swap any adapter through dependency injection.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

pytest   # 255 tests
mypy src/ --strict   # 0 issues
```

---

## Backup

### CLI

#### Simulator (development / testing)

The simulator stores virtual tapes as plain directories on disk — no hardware required.

```bash
lto-backup \
  --source /path/to/records \
  --simulator /path/to/tape-store \
  --capacity-tb 18 \
  --container-size-gb 100
```

#### Real LTO Hardware (Linux, LTFS)

```bash
lto-backup \
  --source /path/to/records \
  --device /dev/nst0 \
  --mount-point /mnt/lto_tape \
  --capacity-tb 18 \
  --container-size-gb 100
```

Prerequisites for LTFS mode:
- `ltfs`, `umount`, and `mt` available on `$PATH`
- Tape formatted with LTFS: `mkltfs -d /dev/nst0`
- Mount point exists: `mkdir -p /mnt/lto_tape`

### Python API

**Simulator:**

```python
from pathlib import Path
from lto_backup.config.backup_config import BackupConfig
from lto_backup.wiring.container import build_backup_service

config = BackupConfig(
    source_root=Path("/path/to/records"),
    tapes_root=Path("/path/to/tape-store"),
    tape_nominal_capacity_bytes=18 * 1_000_000_000_000,   # 18 TB (LTO-9)
    max_container_size_bytes=100 * 1_000_000_000,         # 100 GB containers
)

catalog = build_backup_service(config).run(config)
print(f"Backup complete: {len(catalog.tapes)} tape(s), {len(catalog.source_files)} file(s)")
```

**Real LTO hardware:**

```python
import logging
from pathlib import Path
from lto_backup.config.backup_config import BackupConfig
from lto_backup.config.logging_config import LoggingConfig
from lto_backup.wiring.container import build_ltfs_backup_service

LoggingConfig(verbose=True).configure()

config = BackupConfig(
    source_root=Path("/mnt/records"),
    tapes_root=Path("/mnt/ltfs"),
    tape_nominal_capacity_bytes=12 * 1_000_000_000_000,   # 12 TB (LTO-8)
    max_container_size_bytes=200 * 1_000_000_000,
)

catalog = build_ltfs_backup_service(
    config, device=Path("/dev/nst0"), mount_point=Path("/mnt/ltfs")
).run(config)
```

### CLI Flags

| Flag | Required | Description |
|---|---|---|
| `--source DIR` | yes | Directory tree to back up |
| `--simulator DIR` | one of | Simulator mode: root directory for virtual tapes |
| `--device DEV` | one of | Hardware mode: tape device path (e.g. `/dev/nst0`) |
| `--mount-point DIR` | with `--device` | LTFS mount point (e.g. `/mnt/lto_tape`) |
| `--capacity-tb TB` | yes | Nominal tape capacity in terabytes (e.g. `18` for LTO-9) |
| `--container-size-gb GB` | yes | Maximum container size in gigabytes (e.g. `100`) |
| `--verbose` | no | Enable DEBUG-level logging |

`--simulator` and `--device` are mutually exclusive; exactly one must be supplied.

The `--container-size-gb` value must not exceed the usable tape capacity (nominal minus catalog reserve). Typical values are 100–500 GB. Smaller containers limit the amount of data at risk from a single read error.

Example output:

```
Backup complete. 2 tape(s), 1438 file(s).
```

### On-Disk Layout (simulator)

```
/path/to/tape-store/
  TAPE-<uuid>-001/
    data/
      CNT-<uuid>-00001       ← container blobs (raw bytes)
      CNT-<uuid>-00002
    catalog/
      catalog.json           ← full catalog for the entire backup set
      catalog.sha256         ← SHA-256 of catalog.json
    tape.json                ← simulator metadata (capacity tracking)
  TAPE-<uuid>-002/
    data/
      CNT-<uuid>-00003
    catalog/
      catalog.json
      catalog.sha256
    tape.json
```

### Pipeline

Each run executes five stages in sequence:

1. **Scan** — `SourceScanner` walks the source directory, hashes every file with SHA-256, and records size, modification time, and Unix permission bits (`unix_mode`).
2. **Plan** — `BackupPlanner` iterates packing until the serialized catalog size (including all tape, container, and segment entries with 64-char SHA-256 placeholders, plus the 64-byte checksum file) fits within `reserved_catalog_bytes`. This guarantees enough space is reserved on every tape before data is written.
3. **Hash** — `BackupWriter.compute_sha256s()` reads every source file and pre-computes the SHA-256 of each planned segment. No tape I/O occurs at this stage.
4. **Write** — `BackupWriter.write()` iterates tapes in sequence. For each tape it loads the tape, writes all containers (reading and verifying source files against their scanned SHA-256 — `SourceFileChangedError` if modified), then writes the full catalog to the tape before unloading it. Each physical tape is loaded exactly once.
5. **Catalog** — `CatalogService` assembles the `Catalog` object (filling in the pre-computed segment SHA-256s) and serializes it to `catalog/catalog.json` + `catalog/catalog.sha256`. The catalog is written to each tape immediately before that tape is ejected.

### Tape Switching (multi-tape backups)

When a backup spans more than one tape, `TapeSwitchService` pauses the write pipeline and prompts the operator on the terminal:

```
Please insert tape TAPE-002 (tape 2) and press Enter.
```

The service retries up to 5 times if the tape drive reports the tape is not loaded. No extra configuration is required.

---

## Restore

`lto-restore` reassembles source files from tape using the catalog that was
written to every tape at backup time. Each segment is re-hashed during read;
after all segments are written the full-file SHA-256 is checked against the
catalog. After each file passes full-file verification, the original modification
time and Unix permission bits are restored (`mtime` via `os.utime`, `unix_mode`
via `os.chmod`). On Windows, `os.chmod` only affects the read-only flag; the
Archive attribute is **not** preserved (Windows sets it automatically on file
creation).

### CLI

#### Simulator

```bash
# Catalog read directly from the first tape:
lto-restore \
  --simulator /path/to/tape-store \
  --restore-to /path/to/recovered \
  --first-tape-id TAPE-001

# Catalog already on disk:
lto-restore \
  --simulator /path/to/tape-store \
  --restore-to /path/to/recovered \
  --catalog /path/to/tape-store/TAPE-001/catalog/catalog.json

# Selective restore — only files matching a glob:
lto-restore \
  --simulator /path/to/tape-store \
  --restore-to /path/to/recovered \
  --catalog catalog.json \
  --filter "records/case-001/*"

# With an HTML restore report:
lto-restore \
  --simulator /path/to/tape-store \
  --restore-to /path/to/recovered \
  --catalog catalog.json \
  --report-dir /path/to/reports
```

#### Real LTO Hardware (Linux, LTFS)

```bash
lto-restore \
  --device /dev/nst0 \
  --mount-point /mnt/lto_tape \
  --restore-to /path/to/recovered \
  --first-tape-id TAPE-001
```

### Python API

```python
from pathlib import Path
from lto_backup.config.backup_config import BackupConfig
from lto_backup.wiring.container import build_restore_service

config = BackupConfig(
    source_root=Path("/unused"),          # not used by RestoreService
    tapes_root=Path("/path/to/tape-store"),
    tape_nominal_capacity_bytes=18 * 1_000_000_000_000,
    max_container_size_bytes=100 * 1_000_000_000,
)

service = build_restore_service(config)

# Option A: load catalog from tape
catalog = service.load_catalog_from_tape("TAPE-001")

# Option B: load catalog from a file on disk
from lto_backup.infrastructure.catalog.json_catalog_serializer import JsonCatalogSerializer
catalog = JsonCatalogSerializer().deserialize(
    Path("catalog.json").read_bytes()
)

report = service.restore(catalog, restore_root=Path("/path/to/recovered"))
print(f"{report.files_restored}/{report.files_requested} file(s) restored")
if report.errors:
    for e in report.errors:
        print("ERROR:", e)

# Optional: generate an HTML restore report
from lto_backup.services.restore_report_service import (
    DETAIL_CONTAINER, DETAIL_FILE, RestoreReportService
)

report_path = RestoreReportService().generate(
    catalog,
    report,
    restore_root=Path("/path/to/recovered"),
    filter_glob=None,
    output_dir=Path("/path/to/reports"),
    detail_level=DETAIL_CONTAINER,   # or DETAIL_FILE for per-file status
)
print(f"Report written to {report_path}")
```

### CLI Flags

| Flag | Required | Description |
|---|---|---|
| `--restore-to DIR` | yes | Destination directory for restored files |
| `--first-tape-id ID` | one of | Tape ID to load when reading the catalog from tape |
| `--catalog FILE` | one of | Path to a `catalog.json` file on disk |
| `--filter GLOB` | no | Restore only files whose relative path matches this fnmatch pattern |
| `--report-dir DIR` | no | Write an HTML restore report to this directory |
| `--detail {container,file}` | no | Detail level in the report: `container` (default) or `file` |
| `--simulator DIR` | one of | Simulator mode: root directory for virtual tapes |
| `--device DEV` | one of | Hardware mode: tape device path (e.g. `/dev/nst0`) |
| `--mount-point DIR` | with `--device` | LTFS mount point (e.g. `/mnt/lto_tape`) |
| `--capacity-tb TB` | no | Nominal tape capacity in TB — simulator only (default: `18`) |
| `--verbose` | no | Enable DEBUG-level logging |

`--first-tape-id` and `--catalog` are mutually exclusive; exactly one must be supplied.

Example output:

```
Restore complete. 1438/1438 file(s) restored.
Report written to /path/to/reports/restore-report-<backup_set_id>.html
```

---

## Verification

After backup, `VerificationService` validates tape contents:

- Loads each tape listed in the catalog.
- Re-reads and re-hashes `catalog/catalog.json` and checks it against `catalog/catalog.sha256`.
- Re-reads and re-hashes every data segment and compares against the per-segment SHA-256 in the catalog.
- Returns a list of error strings (empty list means clean).

```python
from pathlib import Path
from lto_backup.config.backup_config import BackupConfig
from lto_backup.infrastructure.catalog.json_catalog_serializer import JsonCatalogSerializer
from lto_backup.infrastructure.filesystem.sha256_file_hasher import Sha256FileHasher
from lto_backup.infrastructure.simulator.simulator_tape_drive import SimulatorTapeDrive
from lto_backup.services.verification_service import VerificationService

tapes = Path("/path/to/tape-store")
verifier = VerificationService(
    SimulatorTapeDrive(tapes, 18 * 1_000_000_000_000),
    JsonCatalogSerializer(),
    Sha256FileHasher(),
)
errors = verifier.verify(catalog)
if errors:
    for e in errors:
        print("CORRUPT:", e)
else:
    print("All tapes verified clean.")
```

## Catalog Format

The catalog is written to every tape as `catalog/catalog.json` (with a companion `catalog/catalog.sha256`). It contains:

| Field | Description |
|---|---|
| `schema_version` | Catalog schema version string (`2.0`) |
| `backup_set_id` | UUID identifying this backup set |
| `created_at` | ISO-8601 timestamp of backup creation |
| `source_root` | Absolute path of the source directory |
| `tapes` | List of tape objects (`tape_id`, `backup_set_id`, `sequence_number`, `nominal_capacity_bytes`, `reserved_catalog_bytes`) |
| `containers` | List of containers (`container_id`, `backup_set_id`, `tape_id`, `sequence_number`, `tape_offset`, `size_bytes`) |
| `source_files` | List of source files (`file_id`, `relative_path`, `absolute_path`, `size_bytes`, `sha256`, `modified_at`, `unix_mode`) — `unix_mode` is an integer or `null`; absent in catalogs created before this feature (deserialized as `None`) |
| `segments` | List of tape segments (`segment_id`, `file_id`, `container_id`, `container_offset`, `source_offset`, `length_bytes`, `sha256`) |

Each segment's `sha256` is the hash of that slice of bytes within the container. Full-file hashes are stored on the `source_files` entries.

To restore a file: look up its segments in the catalog → for each segment, load the tape identified by its container's `tape_id`, read the container file, slice out `container_offset` to `container_offset + length_bytes`.

## Simulator Failure Injection

`SimulatorFailureConfig` allows injecting failures into the simulator for testing:

| Field | Type | Description |
|---|---|---|
| `fail_on_write` | `bool` | Raise `FileWriteError` on every write |
| `fail_on_read` | `bool` | Raise `FileReadError` on every read |
| `fail_on_load` | `bool` | Raise `TapeNotLoadedError` on every load |
| `fail_after_bytes_written` | `int \| None` | Raise `TapeFullError` after N bytes written |
| `failed_tape_ids` | `set[str]` | Only inject failures for these tape IDs |
| `error_message` | `str` | Custom message on injected exceptions |

## Exception Hierarchy

All domain exceptions inherit from `BackupError`:

| Exception | Raised when |
|---|---|
| `BackupPlanError` | A valid backup plan cannot be created (e.g. file larger than tape) |
| `CatalogWriteError` | The catalog cannot be serialized or written to tape |
| `FileWriteError` | A source file segment cannot be written to tape |
| `SourceFileChangedError` | A source file is modified during the backup |
| `TapeFullError` | A write would exceed the tape's usable capacity |
| `TapeNotLoadedError` | A tape drive operation is attempted with no tape loaded |

## Project Structure

```
src/lto_backup/
  cli/                CLI entry point (main.py)
  config/             BackupConfig and LoggingConfig dataclasses
  domain/             Pure frozen dataclasses (Catalog, Tape, TapeSegment, SourceFile, …)
  exceptions/         Domain exception hierarchy (BackupError and subclasses)
  interfaces/         typing.Protocol interfaces for every infrastructure boundary
  infrastructure/
    catalog/          JsonCatalogSerializer
    clock/            SystemClock
    filesystem/       LocalFileSystem, Sha256FileHasher
    simulator/        SimulatorTapeDrive, VirtualTape, SimulatorFailureConfig
    tape/             LinuxLtoTapeDrive (LTFS)
  services/           Business logic: SourceScanner, BackupPlanner, BackupWriter,
                      CatalogService, BackupService, VerificationService, TapeSwitchService,
                      ReportService, RestoreService, RestoreReportService
  wiring/             Composition root (container.py)
tests/
  unit/               Unit tests mirroring src layout
  integration/        Simulator-backed end-to-end tests
  fixtures/           Shared test fixtures
```

## Architecture

- One production class per file.
- Dependency injection throughout — no concrete infrastructure inside services.
- `typing.Protocol` interfaces for every infrastructure boundary.
- Domain objects are frozen dataclasses, free of I/O concerns.
- Strict mypy type checking (`mypy --strict`).
