# lto-backup — Domain Specification

## Domain Concepts

### Backup Set

A backup set is one complete backup operation. It may span multiple tapes.

### Tape

A tape is a logical LTO cartridge. In simulator mode a tape is represented by a directory on disk.

### Container

A container is a fixed-size logical block written as a single file onto a tape. Source files and file-slices are packed sequentially into containers. A container never spans two tapes — it lives entirely on one tape.

The user controls container size via `--container-size-gb`. Typical values: 100 GB–500 GB. Smaller containers reduce recovery scope per read error; larger containers reduce catalog overhead.

### Catalog

A catalog is the complete manifest for the whole backup set. A full copy must be written to **every** tape in the backup set.

The catalog must include:

- Backup set ID
- Source root
- Tape list
- Container list (with tape ID and tape offset per container)
- Source file list
- Segment list (with container ID, container offset, source offset, length, SHA-256)
- Timestamps

### Split Files Across Containers and Tapes

If a source file does not fit in the remaining space of the current container, the remainder spills into the next container. If no container fits on the current tape, a new tape is started.

Example:

```
source file  : records/case-001/video.bin  (140 GB)
container-01 : tape-001, 40 GB remaining   → segment 1 (source_offset=0,    length=40 GB)
container-02 : tape-002, 100 GB capacity   → segment 2 (source_offset=40 GB, length=100 GB)
```

Every segment is recorded in the catalog with its container ID and byte offset within that container.

### Tape Capacity

```
usable_capacity = nominal_capacity - reserved_catalog_bytes
```

`reserved_catalog_bytes` is **not** a user-facing parameter. The planner computes it automatically using a three-pass approach:

1. **Draft catalog** — build a catalog containing only the source file list (no containers or segments yet) and serialize it to measure its baseline size. That size becomes `reserved_catalog_bytes` for every tape.
2. **Pack containers** — distribute source files and file-slices into containers of at most `max_container_size_bytes`, then assign containers to tapes respecting `usable_capacity`.
3. **Finalize** — assemble the full plan with all containers and segments.

---

## Simulator — Virtual Tape Layout

```
<tapes-root>/
  TAPE-001/
    data/
      container-0001.bin
      container-0002.bin
    catalog/
      catalog.json
      catalog.sha256
    tape.json
  TAPE-002/
    data/
    catalog/
    tape.json
```

The simulator must support:

- Load / unload a tape
- Write and read files
- List files
- Capacity tracking
- Raise `TapeFullError` when a write exceeds remaining capacity
- Optional failure injection via `SimulatorFailureConfig`

---

## Implementation Roadmap

All planned work is complete.

| # | Component | Status |
|---|---|---|
| 1 | `SimulatorTapeDrive` | ✓ done |
| 2 | `SourceScanner` | ✓ done |
| 3 | `BackupPlanner` | ✓ done |
| 4 | `BackupWriter` | ✓ done |
| 5 | `CatalogService` | ✓ done |
| 6 | `VerificationService` | ✓ done |
| 7 | `TapeSwitchService` | ✓ done |
| 8 | `LinuxLtoTapeDrive` (LTFS) | ✓ done |
| 9 | `wiring/container.py` (DI composition root) | ✓ done |
| 10 | CLI with `--simulator` / `--device` flag | ✓ done |
| 11 | Simulator integration test (backup → verify) | ✓ done |

---

## Backup Pipeline

Each backup run executes four stages:

1. **Scan** — `SourceScanner` walks `source_root`, computes SHA-256 for every file, records size and `modified_at`.
2. **Plan** — `BackupPlanner` uses the three-pass algorithm to compute `reserved_catalog_bytes`, then packs source files into containers (≤ `max_container_size_bytes` each) and assigns containers to tapes. Files larger than one container are split across container boundaries. `TapeSegment.sha256` is set to `""` at planning time.
3. **Write** — `BackupWriter` iterates tapes and containers in order. For each container it collects all its segments, reads the corresponding file slices from disk, verifies the full-file SHA-256 against the scanned value (raises `SourceFileChangedError` if not), concatenates the slices, writes the container as a single blob to tape, and records per-segment SHA-256s. Returns a `dict[segment_id, sha256]`.
4. **Catalog** — `CatalogService` fills segment SHA-256s via `dataclasses.replace`, serializes the catalog to JSON, and writes `catalog/catalog.json` + `catalog/catalog.sha256` to every tape.

---

## Tape Switching

`TapeSwitchService.request_and_load(tape_id, sequence_number)` prompts the operator via `UserPrompt` to insert the next tape, then calls `TapeDrive.load_tape`. On `TapeNotLoadedError` it retries up to `max_retries` (default 5) times before re-raising.

---

## Verification

`VerificationService.verify(catalog) -> list[str]` iterates every tape in the catalog, loads it, re-hashes `catalog/catalog.json` and compares against `catalog/catalog.sha256`, then for each container on that tape reads the container blob, slices out each segment's bytes, re-hashes them, and compares against `catalog.segments[*].sha256`. Returns a list of error strings; an empty list means all tapes are clean.

---

## LinuxLtoTapeDrive (LTFS)

`LinuxLtoTapeDrive(device: Path, mount_point: Path)` implements `TapeDrive` using LTFS:

- `load_tape` — runs `ltfs {device}` to mount at `{mount_point}`. Raises `TapeNotLoadedError` on failure.
- `unload_tape` — runs `umount {mount_point}` then `mt -f {device} offline`.
- `write_file` — writes to `{mount_point}/data/{filename}`. Raises `TapeFullError` on `ENOSPC`.
- `read_file` — reads from `{mount_point}/data/{filename}`.
- `list_files` — lists `{mount_point}/data/`.

Required system tools: `ltfs`, `umount`, `mt` (on `$PATH`). Tape must be pre-formatted with `mkltfs -d {device}`.

---

## Simulator — Virtual Tape Layout

```
<tapes-root>/
  BACKUP-001/
    data/
      records__case-001__video.bin.part1
      records__case-001__video.bin.part2
    catalog/
      catalog.json
      catalog.sha256
    tape.json
  BACKUP-002/
    data/
    catalog/
    tape.json
```

`SimulatorFailureConfig` enables failure injection for testing:

| Field | Effect |
|---|---|
| `fail_on_write` | Raise `FileWriteError` on every write |
| `fail_on_read` | Raise `FileWriteError` on every read |
| `fail_on_load` | Raise `TapeNotLoadedError` on every load |
| `fail_after_bytes_written` | Raise `TapeFullError` after N bytes written |
| `failed_tape_ids` | Restrict injection to specific tape IDs |

---

## Planner Test Requirements (reference)

1. All files fit in one container on one tape.
2. Multiple files fill multiple containers across multiple tapes.
3. A single large file splits across container boundaries (and tape boundaries).
4. Computed catalog reserve (three-pass) reduces usable capacity.
5. `max_container_size_bytes` exceeding usable capacity raises `BackupPlanError`.
6. Invalid capacity raises `BackupPlanError`.

## Simulator Test Requirements (reference)

1. Load and unload tape.
2. Write until capacity is reached.
3. Raise `TapeFullError` when capacity is exceeded.
4. List written files.
5. Read written files.
6. Failure injection via `SimulatorFailureConfig`.
