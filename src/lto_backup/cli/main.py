"""CLI entry point for lto-backup."""

import argparse
import logging
import sys
from pathlib import Path

from lto_backup.config.backup_config import BackupConfig
from lto_backup.config.logging_config import LoggingConfig
from lto_backup.exceptions.backup_error import BackupError
from lto_backup.wiring.container import build_backup_service, build_ltfs_backup_service

_BYTES_PER_TB = 1_000_000_000_000
_BYTES_PER_GB = 1_000_000_000

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lto-backup",
        description="Back up a file-based records management system to LTO tape.",
    )
    parser.add_argument(
        "--source",
        required=True,
        metavar="DIR",
        help="Source directory to back up.",
    )
    parser.add_argument(
        "--capacity-tb",
        required=True,
        type=float,
        metavar="TB",
        help="Nominal tape capacity in terabytes.",
    )
    parser.add_argument(
        "--container-size-gb",
        required=True,
        type=float,
        metavar="GB",
        help="Maximum container size in gigabytes.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )

    mode_group = parser.add_argument_group("drive mode (choose one)")
    drive = mode_group.add_mutually_exclusive_group(required=True)
    drive.add_argument(
        "--simulator",
        metavar="DIR",
        help="Use simulator: directory where virtual tape directories are stored.",
    )
    drive.add_argument(
        "--device",
        metavar="DEV",
        help="Use real LTFS hardware: tape device path (e.g. /dev/nst0).",
    )

    parser.add_argument(
        "--mount-point",
        metavar="DIR",
        help="LTFS mount point (required when --device is used).",
    )

    args = parser.parse_args()

    if args.device and not args.mount_point:
        parser.error("--mount-point is required when --device is used.")

    LoggingConfig(verbose=args.verbose).configure()

    config = BackupConfig(
        source_root=Path(args.source),
        tapes_root=Path(args.simulator) if args.simulator else Path(args.mount_point),
        tape_nominal_capacity_bytes=int(args.capacity_tb * _BYTES_PER_TB),
        max_container_size_bytes=int(args.container_size_gb * _BYTES_PER_GB),
    )

    try:
        if args.simulator:
            service = build_backup_service(config)
        else:
            service = build_ltfs_backup_service(
                config,
                device=Path(args.device),
                mount_point=Path(args.mount_point),
            )
        catalog = service.run(config)
    except BackupError as exc:
        logger.error("Backup failed: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Backup complete. {len(catalog.tapes)} tape(s), "
        f"{len(catalog.source_files)} file(s)."
    )

