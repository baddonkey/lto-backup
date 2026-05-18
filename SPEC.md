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
- Container list
- Segment list
- Checksums
- File offsets and segment offsets
- Split-file information

### Container Files

Source files are **not** written directly to tape as loose files. The application writes generated backup container files. A container holds one or more file segments plus the metadata needed to verify and restore them.

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
3. **Plan** container and tape assignments using that reserve.
4. **Finalize** the catalog by adding segment records (split metadata is negligible in size). Assert the final catalog fits within the reserved space.

This means the only capacity-related parameter the user provides is `--tape-capacity`.

---

## Simulator — Virtual Tape Layout

```
.simulator_tapes/
  BACKUP-001/
    data/
      backup-000001.container
      backup-000002.container
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

The first pass (repository skeleton, domain classes, interfaces, `JsonCatalogSerializer`, and unit tests) is complete.
`SimulatorTapeDrive` is also implemented.

Remaining work in order:

1. ~~**Simulator tape drive** — `SimulatorTapeDrive` implementing `TapeDrive`~~ ✓ done
2. **Source scanner** — walk source directory, hash files, produce `SourceFile` list
3. **Backup planner** — allocate files to tapes, create segments and containers, produce `BackupPlan`
4. **Backup writer** — execute a `BackupPlan` against a `TapeDrive`
5. **Catalog service** — build and write `Catalog` to every tape
6. **Verification service** — re-read containers, verify checksums
7. **CLI** — wire everything together via `wiring/container.py`
8. **Simulator integration test** — full backup → verify flow against the simulator

### Planner Test Requirements

1. All files fit on one tape.
2. Multiple files span multiple tapes.
3. A single large file splits across tapes.
4. Container max size forces multiple containers.
5. Computed catalog reserve (two-pass) reduces usable capacity.
6. Invalid capacity raises `BackupPlanError`.

### Simulator Test Requirements

1. Load and unload tape.
2. Write until capacity is reached.
3. Raise `TapeFullError` when capacity is exceeded.
4. List written files.
5. Read written files.
