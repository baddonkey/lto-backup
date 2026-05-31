"""Integration tests for the full backup → verify → restore cycle on real LTFS hardware.

These tests require a configured LTO tape drive (or mhVTL virtual drive) accessible
via LTFS.  They are skipped automatically when the required environment variables are
not set, so they never block the normal test suite.

Prerequisites on the test machine (Raspberry Pi or any Linux host):
  - ltfs, mt-st, umount installed and on $PATH
  - Tape device accessible (e.g. /dev/nst0 from mhVTL or real hardware)
  - LTFS mount point directory exists (e.g. /mnt/lto_test)
  - Tape pre-formatted with LTFS:  mkltfs -d /dev/nst0

Required environment variables:
  LTO_DEVICE        Path to the tape device, e.g. /dev/nst0
  LTO_MOUNT_POINT   LTFS mount point directory, e.g. /mnt/lto_test

Optional environment variables:
  LTO_CAPACITY_TB   Nominal tape capacity in TB (default: 1.5 for mhVTL default cartridge)

Example invocation:
  LTO_DEVICE=/dev/nst0 LTO_MOUNT_POINT=/mnt/lto_test pytest tests/integration/test_ltfs_backup_flow.py -v
"""

import os
from pathlib import Path

import pytest

_DEVICE_ENV = "LTO_DEVICE"
_MOUNT_ENV = "LTO_MOUNT_POINT"
_CAPACITY_ENV = "LTO_CAPACITY_TB"

_requires_ltfs = pytest.mark.skipif(
    not (os.environ.get(_DEVICE_ENV) and os.environ.get(_MOUNT_ENV)),
    reason=(
        f"Skipped: set {_DEVICE_ENV} and {_MOUNT_ENV} environment variables "
        "to enable LTFS integration tests."
    ),
)

_BYTES_PER_TB = 1_000_000_000_000
_BYTES_PER_GB = 1_000_000_000


def _ltfs_config(source_root: Path, tapes_root: Path) -> "BackupConfig":  # type: ignore[name-defined]
    from lto_backup.config.backup_config import BackupConfig

    capacity_tb = float(os.environ.get(_CAPACITY_ENV, "1.5"))
    return BackupConfig(
        source_root=source_root,
        tapes_root=tapes_root,
        tape_nominal_capacity_bytes=int(capacity_tb * _BYTES_PER_TB),
        max_container_size_bytes=int(0.5 * _BYTES_PER_GB),  # 500 MB containers
    )


@_requires_ltfs
class TestLtfsSingleTapeBackupVerifyRestore:
    """Single-tape backup → verify → restore cycle on real LTFS."""

    def test_backup_verify_restore_round_trip(self, tmp_path: Path) -> None:
        from lto_backup.config.backup_config import BackupConfig
        from lto_backup.config.logging_config import LoggingConfig
        from lto_backup.infrastructure.catalog.json_catalog_serializer import (
            JsonCatalogSerializer,
        )
        from lto_backup.wiring.container import (
            build_ltfs_backup_service,
            build_ltfs_restore_service,
            build_ltfs_verification_service,
        )

        LoggingConfig(verbose=True).configure()

        device = Path(os.environ[_DEVICE_ENV])
        mount_point = Path(os.environ[_MOUNT_ENV])
        source_root = tmp_path / "source"
        restore_root = tmp_path / "restored"
        source_root.mkdir()
        restore_root.mkdir()

        # Create a small set of source files.
        (source_root / "file_a.txt").write_bytes(b"Hello from file A\n" * 100)
        (source_root / "subdir").mkdir()
        (source_root / "subdir" / "file_b.bin").write_bytes(bytes(range(256)) * 50)

        config = _ltfs_config(source_root, mount_point)

        # --- Backup ---
        backup_svc = build_ltfs_backup_service(config, device=device, mount_point=mount_point)
        catalog = backup_svc.run(config)

        assert len(catalog.tapes) >= 1
        assert len(catalog.source_files) == 2

        # --- Verify ---
        verify_svc = build_ltfs_verification_service(
            config, device=device, mount_point=mount_point
        )
        report = verify_svc.verify(catalog)
        assert report.errors == [], f"Verification errors: {report.errors}"

        # --- Restore ---
        restore_svc = build_ltfs_restore_service(
            config, device=device, mount_point=mount_point
        )
        restore_report = restore_svc.restore(catalog, restore_root)

        assert restore_report.files_requested == 2
        assert restore_report.files_restored == 2
        assert restore_report.errors == []

        # Content must match.
        assert (restore_root / "file_a.txt").read_bytes() == (
            source_root / "file_a.txt"
        ).read_bytes()
        assert (restore_root / "subdir" / "file_b.bin").read_bytes() == (
            source_root / "subdir" / "file_b.bin"
        ).read_bytes()

    def test_backup_creates_catalog_on_tape(self, tmp_path: Path) -> None:
        """Catalog JSON must be readable from the tape after backup."""
        from lto_backup.infrastructure.catalog.json_catalog_serializer import (
            JsonCatalogSerializer,
        )
        from lto_backup.infrastructure.tape.linux_lto_tape_drive import LinuxLtoTapeDrive

        device = Path(os.environ[_DEVICE_ENV])
        mount_point = Path(os.environ[_MOUNT_ENV])
        source_root = tmp_path / "source"
        source_root.mkdir()
        (source_root / "small.txt").write_bytes(b"tiny")

        from lto_backup.config.backup_config import BackupConfig
        from lto_backup.wiring.container import build_ltfs_backup_service

        config = _ltfs_config(source_root, mount_point)
        backup_svc = build_ltfs_backup_service(config, device=device, mount_point=mount_point)
        catalog = backup_svc.run(config)
        first_tape_id = catalog.tapes[0].tape_id

        drive = LinuxLtoTapeDrive(device, mount_point)
        restore_svc_module = __import__(
            "lto_backup.services.restore_service", fromlist=["RestoreService"]
        )
        from lto_backup.services.restore_service import RestoreService
        from lto_backup.wiring.container import build_ltfs_restore_service

        restore_svc = build_ltfs_restore_service(config, device=device, mount_point=mount_point)
        loaded_catalog = restore_svc.load_catalog_from_tape(first_tape_id)

        assert loaded_catalog.backup_set_id == catalog.backup_set_id
        assert len(loaded_catalog.source_files) == len(catalog.source_files)
