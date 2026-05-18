# lto-backup — Domain Specification

## Domain Concepts

### Backup Set

A backup set is one complete backup operation. It may span multiple tapes.

### Tape

A tape is a logical LTO cartridge. In simulator mode a tape is represented by a directory on disk.

### Catalog

A catalog is the complete manifest for the whole backup set. A full copy must be written to **every** tape in the backup set.

The catalog must include:

- Backup set ID
- Source root
- Tape list
- Source file list
- Segment list
- Checksums
- File offsets and tape offsets
- Split-file information

### Plain File Storage

Source files are written directly to tape as plain files. Each file is written sequentially. The catalog records the exact byte offset on tape where each segment starts, enabling precise restore without any intermediate packaging.

### Split Files Across Tapes

If a source file does not fit in the remaining space on the current tape, it is split across tapes.

Example:

```
source file : records/case-001/video.bin
size        : 140 GB
tape 001    : 40 GB remaining  → segment 1 (offset 0,       length 40 GB)
tape 002    : 100 GB usable    → segment 2 (offset 40 GB,   length 100 GB)
```

Every segment must be recorded in the catalog.

### Tape Capacity

```
usable_capacity = nominal_capacity - reserved_catalog_bytes
```

`reserved_catalog_bytes` is **not** a user-facing parameter. The planner computes it automatically using a two-pass approach:

1. **Scan** the source directory to produce the full `list[SourceFile]`.
2. **Build a draft catalog** containing only the file list (no segments yet) and serialize it to measure its size. That measured size becomes `reserved_catalog_bytes` for every tape in the set.
3. **Plan** tape assignments using that reserve.
4. **Finalize** the catalog by adding segment records (split metadata is negligible in size). Assert the final catalog fits within the reserved space.

This means the only capacity-related parameter the user provides is `--tape-capacity`.

---

## Simulator — Virtual Tape Layout

```
.simulator_tapes/
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

The simulator must support:

- Load / unload a tape
- Write bytes and write files
- Read files
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
2. **Plan** — `BackupPlanner` uses the two-pass catalog sizing strategy to compute `reserved_catalog_bytes`, then distributes files and file-slices across tapes. Files larger than one tape are split. `TapeSegment.sha256` is set to `""` at planning time.
3. **Write** — `BackupWriter` streams each segment to the tape drive, verifies the full-file SHA-256 matches the scanned value (raises `SourceFileChangedError` if not), computes per-segment SHA-256 after slicing, and returns a `dict[segment_id, sha256]`.
4. **Catalog** — `CatalogService` fills segment SHA-256s via `dataclasses.replace`, serializes the catalog to JSON, and writes `catalog/catalog.json` + `catalog/catalog.sha256` to every tape.

---

## Tape Switching

`TapeSwitchService.request_and_load(tape_id, sequence_number)` prompts the operator via `UserPrompt` to insert the next tape, then calls `TapeDrive.load_tape`. On `TapeNotLoadedError` it retries up to `max_retries` (default 5) times before re-raising.

---

## Verification

`VerificationService.verify(catalog) -> list[str]` iterates every tape in the catalog, loads it, re-hashes `catalog/catalog.json` and compares against `catalog/catalog.sha256`, then re-hashes every data segment and compares against `catalog.segments[*].sha256`. Returns a list of error strings; an empty list means all tapes are clean.

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

1. All files fit on one tape.
2. Multiple files span multiple tapes.
3. A single large file splits across two tapes.
4. Computed catalog reserve (two-pass) reduces usable capacity.
5. Invalid capacity raises `BackupPlanError`.

## Simulator Test Requirements (reference)

1. Load and unload tape.
2. Write until capacity is reached.
3. Raise `TapeFullError` when capacity is exceeded.
4. List written files.
5. Read written files.
6. Failure injection via `SimulatorFailureConfig`.
