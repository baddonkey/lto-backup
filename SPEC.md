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
- Container list (with tape ID, tape offset, size in bytes, and SHA-256 per container)
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

`reserved_catalog_bytes` is **not** a user-facing parameter. The planner computes it automatically using an iterative packing approach:

1. **Draft estimate** — build a catalog with only the source file list and serialize it to get an initial lower bound for `reserved_catalog_bytes`. Add 64 bytes for the companion `catalog.sha256` file. Compute `usable_capacity = nominal_capacity - reserved_catalog_bytes`.
2. **Pack** — distribute source files and file-slices into containers of at most `max_container_size_bytes`, then assign containers to tapes respecting `usable_capacity`. Segment SHA-256 fields use a 64-character placeholder so the estimated size matches the final serialized size.
3. **Measure and re-pack** — serialize the full catalog (tapes + containers + segments + source files) and measure its size plus the 64-byte checksum file. If this size exceeds the current `reserved_catalog_bytes`, update the reserve and repeat from step 2. Repeat until stable (typically converges in ≤2 iterations).

---

## Class Diagram

See [docs/tapedrive.mmd](docs/tapedrive.mmd) for the Mermaid class diagram covering the `TapeDrive` protocol, its implementations (`LinuxLtoTapeDrive`, `SimulatorTapeDrive`), simulator internals, and `TapeSwitchService`.

See [docs/overview.mmd](docs/overview.mmd) for the full Mermaid class diagram covering all domain objects, interfaces, infrastructure adapters, services, and exceptions.

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

Each backup run executes five stages:

1. **Scan** — `SourceScanner` walks `source_root`, computes SHA-256 for every file, records size and `modified_at`.
2. **Plan** — `BackupPlanner` uses the iterative packing algorithm to compute `reserved_catalog_bytes`, then packs source files into containers (≤ `max_container_size_bytes` each) and assigns containers to tapes. Files larger than one container are split across container boundaries. Segment `sha256` fields hold 64-char placeholders at planning time.
3. **Hash** — `BackupWriter.compute_sha256s()` reads every source file, verifies the full-file SHA-256 against the scanned value (`SourceFileChangedError` if modified), and returns a `dict[segment_id, sha256]` of per-segment hashes. No tape I/O occurs.
4. **Write** — `BackupWriter.write()` accepts a `post_tape_callback: Callable[[TapeDrive], None]`. For each tape it loads the tape, writes all containers (reading and verifying source files), then calls `post_tape_callback` with the still-loaded tape drive before unloading. Each physical tape is handled exactly once. After each container is streamed to the tape, the writer reads it back via `TapeDrive.read_file_segment` and re-hashes the bytes; on mismatch it raises `ContainerVerificationError` and aborts the run.
5. **Catalog** — `BackupService` passes `catalog_service.write_catalog_to_tape` as the callback. `CatalogService.build_catalog()` fills segment SHA-256s from step 3 via `dataclasses.replace`, serializes to JSON, and writes `catalog/catalog.json` + `catalog/catalog.sha256` to every tape during its single load in step 4.

---

## Tape Switching

`TapeSwitchService.request_and_load(tape_id, sequence_number)` prompts the operator via `UserPrompt` to insert the next tape, then calls `TapeDrive.load_tape`. On `TapeNotLoadedError` it retries up to `max_retries` (default 5) times before re-raising.

After a successful load it calls `TapeDrive.read_tape_id()` and compares the recorded identity against the requested `tape_id`. If the values disagree (operator inserted the wrong cartridge), the service unloads the tape and raises `WrongTapeError`. A blank recorded identity (freshly formatted, never-written tape) is accepted so that initial backups can claim new cartridges.

---

## Verification

`VerificationService.verify(catalog) -> list[str]` iterates every tape in the catalog, loads it, re-hashes `catalog/catalog.json` and compares against `catalog/catalog.sha256`, then for each container on that tape (processed in `tape_offset` order so the drive streams forward) it first reads the container blob and verifies its SHA-256 against `catalog.containers[*].sha256`. On a container-hash mismatch it records an error and skips the segment-level checks for that container; on a match it slices out each segment's bytes, re-hashes them, and compares against `catalog.segments[*].sha256`. Returns a list of error strings; an empty list means all tapes are clean.

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
4. Computed catalog reserve (iterative) reduces usable capacity and accounts for the 64-byte checksum file.
5. Invalid capacity raises `BackupPlanError`.

## Simulator Test Requirements (reference)

1. Load and unload tape.
2. Write until capacity is reached.
3. Raise `TapeFullError` when capacity is exceeded.
4. List written files.
5. Read written files.
6. Failure injection via `SimulatorFailureConfig`.
