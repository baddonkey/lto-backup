# lto-backup — Domain Specification

## Domain Concepts

### Backup Set

A backup set is one complete backup operation. It may span multiple tapes.

### Tape

A tape is a logical LTO cartridge. In simulator mode a tape is represented by a directory on disk.

### Container

A container is a fixed-size logical block written as a single file onto a tape. Source files and file-slices are packed sequentially into containers. A container never spans two tapes — it lives entirely on one tape.

The user controls container size via `--container-size-gb` (default **5 GB**). Typical values: 1 GB–50 GB. Smaller containers reduce recovery scope per read error and keep the catalog reserve modest; larger containers reduce per-container metadata overhead but make a single bad container costlier to lose.

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

## Out of Scope

The following concerns are intentionally **not** handled by lto-backup and must be solved by surrounding tooling or hardware:

- **Compression** — relies on the LTO drive's built-in hardware compression. The application writes raw bytes and computes SHA-256 over uncompressed data.
- **Encryption** — out of scope. Apply encryption at the storage layer upstream (e.g. LUKS on the source filesystem) or use LTO hardware encryption (`stenc`) on the drive. The catalog format does not record encryption metadata.
- **Incremental / differential backups** — every run produces a complete, self-contained backup set. There is no notion of a parent backup, changed-file detection, or merging across sets. Re-running selectively over a subset of sources is the operator's responsibility.
- **Deduplication** — identical content stored twice is written twice.

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
| 10 | CLI `lto-backup` with `--simulator` / `--device` flag | ✓ done |
| 11 | Simulator integration test (backup → verify) | ✓ done |
| 12 | `RestoreService` | ✓ done |
| 13 | CLI `lto-restore` with `--simulator` / `--device` flag | ✓ done |
| 14 | Simulator integration test (backup → restore) | ✓ done |

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

## Restore

`RestoreService` reassembles source files from tape back to a restore root directory.

### `load_catalog_from_tape(tape_id: str) -> Catalog`

Loads the named tape, reads `catalog/catalog.json`, deserializes it, and unloads the tape in a `finally` block. Raises `RestoreError` on any failure.

### `restore(catalog, restore_root, filter_glob) -> RestoreReport`

1. **Filter** — select `source_files` whose `relative_path` matches `filter_glob` (fnmatch); if omitted all files are selected.
2. **Build lookups** — `container_by_id` and `segments_by_container` from filtered segments only.
3. **Iterate tapes** — sorted by `sequence_number`; tapes with no relevant containers are skipped. Each tape is requested via `TapeSwitchService.request_and_load` so the operator is prompted.
4. **Per container** — containers on each tape are processed in `tape_offset` order so the drive streams forward. For each segment:
   - Read in ≤4 MiB chunks via `TapeDrive.read_file_segment(container_id, container_offset + pos, n)`.
   - Write chunks via `FileSystem.write_segment(restore_root / relative_path, source_offset + pos, chunk)` — offset = 0 creates the file (making parent directories); offset > 0 seeks into the existing file.
   - Hash the chunk stream with SHA-256 and compare to `segment.sha256`; record an error on mismatch.
5. **Full-file check** — after all tapes, hash each successfully-restored file and compare to `source_file.sha256`; record an error on mismatch.
6. Return `RestoreReport(files_requested, files_restored, errors)`. Segment-level errors are non-fatal — other files continue to be restored.

### `RestoreReport`

| Field | Type | Description |
|---|---|---|
| `files_requested` | `int` | Number of files selected (after filter) |
| `files_restored` | `int` | Files where all segments verified clean |
| `errors` | `list[str]` | Segment or full-file SHA-256 mismatch descriptions |

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
| `fail_on_read` | Raise `FileReadError` on every read |
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

## Restore Test Requirements (reference)

1. Single file, single segment, one tape — file reassembled with correct content, no errors.
2. Single file, two segments in two containers on the same tape — reassembled correctly.
3. Single file spanning two tapes — both tapes loaded in sequence order, file reassembled.
4. `filter_glob` — only matching files restored; non-matching files not written.
5. Segment SHA-256 mismatch — error in report; other files still restored.
6. `load_catalog_from_tape` — returns deserialized catalog; tape unloaded afterward.
7. Tape load failure — raises `RestoreError`.
