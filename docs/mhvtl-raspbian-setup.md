# mhVTL + LTFS Setup on Raspberry Pi (Raspbian / Debian Trixie, ARM64)

Tested on: Raspberry Pi 5, Debian 13 "trixie", kernel `6.12.75+rpt-rpi-2712`, aarch64.

---

## 1. Prerequisites

```bash
sudo apt-get install -y \
  build-essential git bc \
  linux-headers-$(uname -r) \
  zlib1g-dev libzip-dev \
  lsscsi sg3-utils mt-st
```

### Create a `restorecon` stub

mhVTL's `make install` calls `restorecon` (an SELinux tool not present on Debian). Create a no-op stub:

```bash
sudo tee /usr/local/bin/restorecon > /dev/null << 'EOF'
#!/bin/sh
exit 0
EOF
sudo chmod +x /usr/local/bin/restorecon
```

---

## 2. Build and Install mhVTL

```bash
git clone https://github.com/markh794/mhvtl.git
cd mhvtl
make
sudo make install
```

### Load the kernel module

```bash
sudo modprobe mhvtl
```

To load it automatically on boot:

```bash
echo mhvtl | sudo tee /etc/modules-load.d/mhvtl.conf
```

### Initialise the virtual media library

```bash
sudo /etc/init.d/mhvtl start    # or: sudo make_vtl_media
```

---

## 3. Configure LTO-9 Drives

Edit `/etc/mhvtl/device.conf`. Replace any default drive entries with IBM LTO-9 drives. A minimal working config for one library with two LTO-9 drives:

```
Library: 10 CHANNEL: 0 TARGET: 0 LUN: 0
  Vendor identification: STK
  Product identification: L700
  Unit serial number: XYZZY_10
  NAA: 10:22:33:44:ab:cd:ef:00
  Home directory: /opt/mhvtl
  PERSIST: False

Drive: 11 CHANNEL: 0 TARGET: 1 LUN: 0
  Library ID: 10 Slot: 1
  Vendor identification: IBM
  Product identification: ULT3580-TD9
  Unit serial number: XYZZY_A1
  NAA: 10:22:33:44:ab:cd:ef:01
  Home directory: /opt/mhvtl
  PERSIST: False

Drive: 12 CHANNEL: 0 TARGET: 2 LUN: 0
  Library ID: 10 Slot: 2
  Vendor identification: IBM
  Product identification: ULT3580-TD9
  Unit serial number: XYZZY_A2
  NAA: 10:22:33:44:ab:cd:ef:02
  Home directory: /opt/mhvtl
  PERSIST: False
```

> **Important**: LTFS requires IBM LTO drives (`ULT3580-TD9` for LTO-9). Other vendors (e.g. STK T10000B) cause firmware-check failures or segfaults in LTFS.

### Create virtual tape media

```bash
sudo mktape -m E01001L9 -s 9 -t lto9 -d /opt/mhvtl/10
```

(`-s 9` = slot 9 in library 10; repeat for each tape you need)

### Start the daemons

```bash
sudo systemctl start vtllibrary@10 vtltape@11 vtltape@12
sudo systemctl enable vtllibrary@10 vtltape@11 vtltape@12
```

### Verify devices appeared

```bash
lsscsi        # should show the library and two tape drives
ls /dev/st*   # /dev/st0, /dev/st1
ls /dev/sg*   # /dev/sg0, /dev/sg1
```

Drive 11 → `/dev/st0` / `/dev/sg0`  
Drive 12 → `/dev/st1` / `/dev/sg1`

### Add your user to the `tape` group

```bash
sudo usermod -aG tape $USER
# Log out and back in, or: newgrp tape
```

### Load a tape into a drive

```bash
sudo vtlcmd 11 load E01001L9
```

Unload:

```bash
sudo vtlcmd 11 unload E01001L9
```

---

## 4. Build and Install LTFS

LTFS 2.4.x is not packaged for ARM64 Debian — build from source.

### Install LTFS build dependencies

```bash
sudo apt-get install -y \
  autoconf automake libtool pkg-config \
  libfuse-dev libxml2-dev libicu-dev \
  libsnmp-dev uuid-dev libssl-dev \
  perl libkeras-dev
```

> If `libkeras-dev` is not available, omit it — LTFS will build without it.

### Clone and patch

```bash
git clone https://github.com/LinearTapeFileSystem/ltfs.git
cd ltfs
git submodule update --init --recursive
```

LTFS's `configure.ac` uses `pkg-config icu` which was split into `icu-uc` and `icu-i18n` in modern Debian. Patch it:

```bash
sed -i 's/PKG_CHECK_MODULES(\[ICU\], \[icu\]/PKG_CHECK_MODULES([ICU], [icu-uc icu-i18n]/' configure.ac
```

### Build and install

```bash
./autogen.sh
./configure
make -j$(nproc)
sudo make install
sudo ldconfig
```

Verify:

```bash
ltfs --version
mkltfs --version
```

---

## 5. Sudoers Rule for lto-backup

LTFS must be mounted as root (so `allow_other` is set, giving non-root users access to the FUSE filesystem). Add a passwordless sudoers rule:

```bash
echo "$(whoami) ALL=(root) NOPASSWD: /usr/local/bin/ltfs, /usr/bin/umount" \
  | sudo tee /etc/sudoers.d/lto-backup
sudo chmod 440 /etc/sudoers.d/lto-backup
```

---

## 6. Format a Tape with LTFS

The tape serial number must be exactly **6 alphanumeric characters**.

```bash
sudo vtlcmd 11 load E01001L9
sudo mkltfs --device /dev/sg0 --tape-serial TAP001 --volume-name TAPE001 --force
```

> Always format as root so the LTFS root directory gets `mode=0777`, allowing non-root writes via the `allow_other` FUSE mount.

---

## 7. Run lto-backup

```bash
lto-backup \
  --source /path/to/source \
  --device /dev/sg0 \
  --mount-point /mnt/lto_tape \
  --capacity-tb 18 \
  --container-size-gb 100
```

- `--device` must be the **SCSI generic device** (`/dev/sg0`), not the tape device (`/dev/nst0`). LTFS's `sg` backend requires it.
- Press Enter when prompted to insert the tape (it is already loaded via `vtlcmd`).

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `LTFS30210I Cannot open device: failed to open /dev/sg0 (13)` | Not in `tape` group | `newgrp tape` or re-login |
| `LTFS30210I Cannot open device: failed to open /dev/sg0 (16)` | Device busy — stale LTFS process | `sudo umount /mnt/lto_tape && sudo lsof /dev/sg0` to find and kill |
| `LTFS30200I Failed to execute SG_IO ioctl` | Using `/dev/nst0` instead of `/dev/sg0` | Use `--device /dev/sg0` |
| `fusermount: user has no write access to mountpoint` | Mount point owned by root | `sudo chown $USER:$USER /mnt/lto_tape` |
| `PermissionError: /mnt/lto_tape/.tape_id` | Mount point owned by root | `sudo chown $USER:$USER /mnt/lto_tape` |
| `LTFS11220E Medium check failed: extra blocks detected` | Index not written on last unmount | Run `sudo ltfsck -f /dev/sg0` to recover |
| `LTFS11083E Cannot write index … Medium Not Present` | `mt offline` ran before LTFS finished writing the index | Do **not** call `mt offline` while LTFS is mounted |
| `LTFS15029E Tape serial must be 6 characters` | Serial too long | Use exactly 6 chars, e.g. `TAP001` |
