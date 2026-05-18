# lto-backup

A Python 3.12+ application that backs up a file-based records management system to LTO tapes.

## Features

- Scan a source directory and create a backup plan.
- Split backups across multiple tapes.
- Split large files across tape boundaries.
- Write source files directly to tape (plain files, no intermediate packaging).
- Store a full catalog on every tape in the backup set.
- Simulate a tape drive on disk for development and testing.
- Pluggable design — swap the simulator for real LTO hardware.

## Quick Start

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install project + dev dependencies
pip install -e '.[dev]'

# Run tests
pytest

# Type-check
mypy
```

## Usage

### Simulator (development / testing)

The simulator stores virtual tapes as directories on disk — no hardware required.

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

Prerequisites:
- LTFS installed (`ltfs`, `umount`, `mt` available on `$PATH`)
- Tape formatted with LTFS (`mkltfs -d /dev/nst0`)
- Mount point directory exists (`mkdir -p /mnt/lto_tape`)

| Flag | Required | Description |
|---|---|---|
| `--source DIR` | yes | Directory tree to back up |
| `--simulator DIR` | one of | Simulator mode: directory for virtual tape directories |
| `--device DEV` | one of | Hardware mode: tape device path (e.g. `/dev/nst0`) |
| `--mount-point DIR` | with `--device` | LTFS mount point (e.g. `/mnt/lto_tape`) |
| `--capacity-tb TB` | yes | Nominal tape capacity in terabytes (e.g. `18` for LTO-9) |
| `--verbose` | no | Enable DEBUG-level logging |

Example output:

```
Backup complete. 2 tape(s), 1438 file(s).
```

### Tape switching (multi-tape backups)

When a backup spans multiple tapes, the operator is prompted on the terminal to
insert each successive tape before writing continues. This is handled automatically
by `TapeSwitchService` — no extra configuration is needed.

## Project Structure

```
src/lto_backup/
  cli/            CLI entry point
  config/         Backup configuration dataclass
  domain/         Pure domain dataclasses
  exceptions/     Domain exception hierarchy
  interfaces/     Protocol interfaces for infrastructure
  infrastructure/ Concrete adapters (filesystem, simulator, tape)
  services/       Business logic services
  wiring/         Dependency injection composition root
tests/
  unit/           Unit tests mirroring src layout
  integration/    Simulator-backed end-to-end tests
  fixtures/       Shared test fixtures
```

## Architecture

- One production class per file.
- Dependency injection throughout — no concrete infrastructure inside services.
- `typing.Protocol` interfaces for every infrastructure boundary.
- Domain objects are free of I/O and infrastructure concerns.
- Strict mypy type checking.
