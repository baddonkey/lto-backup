# lto-backup

A Python 3.12+ application that backs up a file-based records management system to LTO tapes.

## Features

- Scan a source directory and produce a deterministic backup plan.
- Distribute the backup across multiple tapes automatically.
- Split files that are larger than a single tape across tape boundaries.
- Write source files as plain files directly onto tape — no intermediate packaging.
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

pytest   # 107 tests
mypy     # strict, 0 issues
```

## Usage

### Simulator (development / testing)

The simulator stores virtual tapes as plain directories on disk.

```bash
lto-backup \
  --source /path/to/records \
  --simulator /path/to/tape-store \
  --capacity-tb 18
```

### Real LTO Hardware (Linux, LTFS)

```bash
lto-backup \
  --source /path/to/records \
  --device /dev/nst0 \
  --mount-point /mnt/lto_tape \
  --capacity-tb 18
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
| `--verbose` | no | Enable DEBUG-level logging |

`--simulator` and `--device` are mutually exclusive; exactly one must be supplied.

Example output:

```
Backup complete. 2 tape(s), 1438 file(s).
```

## Backup Pipeline

Each run executes four stages in sequence:

1. **Scan** — `SourceScanner` walks the source directory, hashes every file with SHA-256, and records size and modification time.
2. **Plan** — `BackupPlanner` distributes files and file-slices across tapes to fit within the nominal capacity. Files larger than one tape are split across tape boundaries.
3. **Write** — `BackupWriter` streams each file segment to tape, computes a per-segment SHA-256 after slicing, and detects source files modified mid-backup (`SourceFileChangedError`).
4. **Catalog** — `CatalogService` assembles a `Catalog` containing every tape, source file, and segment (with checksums), serializes it to JSON, and writes it to every tape.

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
| `schema_version` | Catalog schema version string |
| `backup_set_id` | UUID identifying this backup set |
| `created_at` | ISO-8601 timestamp of backup creation |
| `source_root` | Absolute path of the source directory |
| `tapes` | List of tape objects (`tape_id`, `label`, `sequence_number`) |
| `source_files` | List of source files (`file_id`, `path`, `size_bytes`, `sha256`, `modified_at`) |
| `segments` | List of tape segments (`segment_id`, `file_id`, `tape_id`, `tape_offset`, `source_offset`, `length_bytes`, `sha256`) |

Each segment's `sha256` is the hash of that slice of bytes, not the full-file hash. Full-file hashes are stored on the `source_files` entries.

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
