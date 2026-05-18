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
python -m venv .venv
source .venv/bin/activate

# Install project + dev dependencies
pip install -e '.[dev]'

# Run tests
pytest

# Type-check
mypy
```

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
