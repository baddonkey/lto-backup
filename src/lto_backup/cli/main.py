"""CLI entry point for lto-backup."""

import argparse
import sys
from pathlib import Path

from lto_backup.config.backup_config import BackupConfig
from lto_backup.config.logging_config import LoggingConfig
from lto_backup.exceptions.backup_error import BackupError
from lto_backup.wiring.container import build_backup_service

_BYTES_PER_TB = 1_000_000_000_000


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
        "--tapes-root",
        required=True,
        metavar="DIR",
        help="Directory where simulator tape directories are stored.",
    )
    parser.add_argument(
        "--capacity-tb",
        required=True,
        type=float,
        metavar="TB",
        help="Nominal tape capacity in terabytes.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    args = parser.parse_args()

    LoggingConfig(verbose=args.verbose).configure()

    config = BackupConfig(
        source_root=Path(args.source),
        tapes_root=Path(args.tapes_root),
        tape_nominal_capacity_bytes=int(args.capacity_tb * _BYTES_PER_TB),
    )

    try:
        service = build_backup_service(config)
        catalog = service.run(config)
    except BackupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Backup complete. {len(catalog.tapes)} tape(s), "
        f"{len(catalog.source_files)} file(s)."
    )

