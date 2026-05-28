# lto-backup

A Python 3.12+ application that backs up a file-based records management system to LTO tapes.

## Features

- Scan a source directory and produce a deterministic backup plan.
- Pack source files into fixed-size containers written as single blobs onto tape.
- Distribute containers across multiple tapes automatically.
- Split files that are larger than one container across container and tape boundaries.
- Store a full JSON catalog on every tape in the backup set for self-contained recovery.
- Verify tape contents against the catalog checksums after backup.
- Simulate a tape drive on disk for development and testing — no hardware required.
- Real LTO hardware support via LTFS on Linux.
- Pluggable design: swap any adapter through dependency injection.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

pytest   # 114 tests
mypy src/ --strict   # 0 issues
```

## Usage

### Simulator (development / testing)

The simulator stores virtual tapes as plain directories on disk — no hardware
required.

**CLI:**

```bash
lto-backup \
  --source /path/to/records \
  --simulator /path/to/tape-store \
  --capacity-tb 18 \
  --container-size-gb 100
```

**Python API:**

```python
from pathlib import Path
from lto_backup.config.backup_config import BackupConfig
from lto_backup.infrastructure.catalog.json_catalog_serializer import JsonCatalogSerializer
from lto_backup.infrastructure.filesystem.sha256_file_hasher import Sha256FileHasher
from lto_backup.infrastructure.simulator.simulator_tape_drive import SimulatorTapeDrive
from lto_backup.services.verification_service import VerificationService
from lto_backup.wiring.container import build_backup_service

source = Path("/path/to/records")
tapes  = Path("/path/to/tape-store")
tapes.mkdir(parents=True, exist_ok=True)

config = BackupConfig(
    source_root=source,
    tapes_root=tapes,
    tape_nominal_capacity_bytes=18 * 1_000_000_000_000,   # 18 TB (LTO-9)
    max_container_size_bytes=100 * 1_000_000_000,         # 100 GB containers
)

# --- backup ---
catalog = build_backup_service(config).run(config)
print(f"Backup complete: {len(catalog.tapes)} tape(s), {len(catalog.source_files)} file(s)")

# --- verify ---
verifier = VerificationService(
    SimulatorTapeDrive(tapes, config.tape_nominal_capacity_bytes),
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

**On-disk layout after a two-tape backup:**

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

### Real LTO Hardware (Linux, LTFS)

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

## Backup Pipeline

Each run executes four stages in sequence:

1. **Scan** — `SourceScanner` walks the source directory, hashes every file with SHA-256, and records size and modification time.
2. **Plan** — `BackupPlanner` iterates packing until the serialized catalog size (including all tape, container, and segment entries with 64-char SHA-256 placeholders, plus the 64-byte checksum file) fits within `reserved_catalog_bytes`. This guarantees enough space is reserved on every tape before data is written.
3. **Hash** — `BackupWriter.compute_sha256s()` reads every source file and pre-computes the SHA-256 of each planned segment. No tape I/O occurs at this stage.
4. **Write** — `BackupWriter.write()` iterates tapes in sequence. For each tape it loads the tape, writes all containers (reading and verifying source files against their scanned SHA-256 — `SourceFileChangedError` if modified), then writes the full catalog to the tape before unloading it. Each physical tape is loaded exactly once.
5. **Catalog** — `CatalogService` assembles the `Catalog` object (filling in the pre-computed segment SHA-256s) and serializes it to `catalog/catalog.json` + `catalog/catalog.sha256`. The catalog is written to each tape immediately before that tape is ejected.

## Tape Switching (multi-tape backups)

When a backup spans more than one tape, `TapeSwitchService` pauses the write pipeline and prompts the operator on the terminal:

```
Please insert tape TAPE-002 (tape 2) and press Enter.
```

The service retries up to 5 times if the tape drive reports the tape is not loaded. No extra configuration is required.

## Verification

After backup, `VerificationService` can be used to validate tape contents:

- Loads each tape listed in the catalog.
- Re-reads and re-hashes `catalog/catalog.json` and checks it against the stored `catalog/catalog.sha256` file.
- Re-reads and re-hashes every data segment and compares against the per-segment SHA-256 recorded in the catalog.
- Returns a list of error strings (empty list means clean).

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
| `source_files` | List of source files (`file_id`, `relative_path`, `absolute_path`, `size_bytes`, `sha256`, `modified_at`) |
| `segments` | List of tape segments (`segment_id`, `file_id`, `container_id`, `container_offset`, `source_offset`, `length_bytes`, `sha256`) |

Each segment's `sha256` is the hash of that slice of bytes within the container. Full-file hashes are stored on the `source_files` entries.

To restore a file: look up its segments in the catalog → for each segment, load the tape identified by its container's `tape_id`, read the container file, slice out `container_offset` to `container_offset + length_bytes`.

## Simulator Failure Injection

`SimulatorFailureConfig` allows injecting failures into the simulator for testing:

| Field | Type | Description |
|---|---|---|
| `fail_on_write` | `bool` | Raise `FileWriteError` on every write |
| `fail_on_read` | `bool` | Raise `FileWriteError` on every read |
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
                      CatalogService, BackupService, VerificationService, TapeSwitchService
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

## Using the API directly (without the CLI)

You can drive a real LTO drive from a Python script by calling the wiring helpers directly:

```python
import logging
from pathlib import Path

from lto_backup.config.backup_config import BackupConfig
from lto_backup.config.logging_config import LoggingConfig
from lto_backup.wiring.container import build_ltfs_backup_service

SOURCE      = Path("/mnt/records")
DEVICE      = Path("/dev/nst0")        # no-rewind tape device
MOUNT_POINT = Path("/mnt/ltfs")        # empty LTFS mount point

LoggingConfig(verbose=True).configure()
logger = logging.getLogger(__name__)

config = BackupConfig(
    source_root=SOURCE,
    tapes_root=MOUNT_POINT,
    tape_nominal_capacity_bytes=12 * 1_000_000_000_000,   # 12 TB (LTO-8)
    max_container_size_bytes=200 * 1_000_000_000,          # 200 GB containers
)

service = build_ltfs_backup_service(config, device=DEVICE, mount_point=MOUNT_POINT)
catalog = service.run(config)

logger.info("Backup complete. Set: %s, tapes: %d, files: %d",
            catalog.backup_set_id, len(catalog.tapes), len(catalog.source_files))
```

`build_ltfs_backup_service` wires `LinuxLtoTapeDrive` in place of the simulator — everything else (scanner, planner, writer, catalog service) is identical.
Replace `build_ltfs_backup_service` with `build_backup_service` and pass a `tapes_root` directory to switch to the simulator.
