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
  --source   /path/to/records \
  --tapes-root /path/to/tape-store \
  --capacity-tb 18
```

| Flag | Required | Description |
|---|---|---|
| `--source DIR` | yes | Directory tree to back up |
| `--tapes-root DIR` | yes | Directory where simulator tape directories are created |
| `--capacity-tb TB` | yes | Nominal tape capacity in terabytes (e.g. `18` for LTO-9) |
| `--verbose` | no | Enable DEBUG-level logging |

Example output:

```
Backup complete. 2 tape(s), 1438 file(s).
```

### Real LTO Hardware (Linux, LTFS)

For production use on a Linux host with an LTFS-formatted LTO drive, wire the
`LinuxLtoTapeDrive` adapter in code (the CLI currently targets the simulator):

```python
from pathlib import Path
from lto_backup.config.backup_config import BackupConfig
from lto_backup.wiring.container import build_ltfs_backup_service

config = BackupConfig(
    source_root=Path("/mnt/records"),
    tapes_root=Path("/mnt/records"),        # unused by LTFS driver
    tape_nominal_capacity_bytes=18_000_000_000_000,
)
service = build_ltfs_backup_service(
    config,
    device=Path("/dev/nst0"),
    mount_point=Path("/mnt/lto_tape"),
)
catalog = service.run(config)
```

Prerequisites:
- LTFS installed (`ltfs`, `umount`, `mt` available on `$PATH`)
- Tape formatted with LTFS (`mkltfs -d /dev/nst0`)
- Mount point directory exists (`mkdir -p /mnt/lto_tape`)

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
