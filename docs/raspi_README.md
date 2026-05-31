# lto-backup on Raspberry Pi — Quick Reference

For the full mhVTL + LTFS installation guide see [mhvtl-raspbian-setup.md](mhvtl-raspbian-setup.md).

---

## First-Time Tape Preparation

Only needed once per new tape (or after a full reformat).

```bash
sudo vtlcmd 11 load E01001L9
sudo mkltfs --device /dev/sg0 --tape-serial TAP001 --volume-name TAPE001 --force
```

---

## Backup

```bash
# 1. Load tape into drive
sudo vtlcmd 11 load E01001L9

# 2. Run backup — press Enter when prompted to insert the tape
lto-backup \
  --source $HOME/Downloads \
  --device /dev/sg0 \
  --mount-point /mnt/lto_tape \
  --capacity-tb 18 \
  --container-size-gb 100
```

---

## Restore

```bash
# 1. Load tape into drive
sudo vtlcmd 11 load E01001L9

# 2. Mount and read the tape ID written during backup
sudo ltfs -o devname=/dev/sg0 /mnt/lto_tape
TAPE_ID=$(sudo cat /mnt/lto_tape/.tape_id)
echo "Tape ID: $TAPE_ID"

# 3. Unmount — lto-restore will remount it internally
sudo umount /mnt/lto_tape
sudo vtlcmd 11 unload E01001L9
sudo vtlcmd 11 load E01001L9

# 4. Restore files
mkdir -p /tmp/lto_restore
lto-restore \
  --restore-to /tmp/lto_restore \
  --first-tape-id "$TAPE_ID" \
  --device /dev/sg0 \
  --mount-point /mnt/lto_tape
```

---

## Device Reference

| Device | Purpose |
|---|---|
| `/dev/sg0` | SCSI generic — used by `ltfs` and `mkltfs` |
| `/dev/st0` / `/dev/nst0` | Tape device — used by `mt` (not needed for normal backup/restore) |
| `/mnt/lto_tape` | LTFS FUSE mount point |

Drive 11 = `/dev/sg0`, Drive 12 = `/dev/sg1`.
