"""CLI entry point for lto-restore."""

import argparse
import logging
import sys
from pathlib import Path

from lto_backup import __version__
from lto_backup.config.backup_config import BackupConfig
from lto_backup.config.logging_config import LoggingConfig
from lto_backup.exceptions.backup_error import BackupError
from lto_backup.infrastructure.catalog.json_catalog_serializer import JsonCatalogSerializer
from lto_backup.services.restore_report_service import (
    DETAIL_CONTAINER,
    DETAIL_FILE,
    RestoreReportService,
)
from lto_backup.wiring.container import (
    build_ltfs_restore_service,
    build_restore_service,
)

_BYTES_PER_TB = 1_000_000_000_000

logger = logging.getLogger(__name__)


def restore_main() -> None:
    parser = argparse.ArgumentParser(
        prog="lto-restore",
        description="Restore files from an LTO tape backup set.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--restore-to",
        required=True,
        metavar="DIR",
        help="Destination directory for restored files.",
    )
    parser.add_argument(
        "--catalog",
        metavar="FILE",
        help=(
            "Path to a catalog.json file on disk. "
            "If omitted, the catalog is read from the first tape "
            "(requires --first-tape-id)."
        ),
    )
    parser.add_argument(
        "--first-tape-id",
        metavar="ID",
        help=(
            "Tape ID to load when reading the catalog from tape "
            "(required when --catalog is not given)."
        ),
    )
    parser.add_argument(
        "--filter",
        metavar="GLOB",
        help="Restore only files whose relative path matches this fnmatch pattern.",
    )
    parser.add_argument(
        "--capacity-tb",
        type=float,
        default=18.0,
        metavar="TB",
        help=(
            "Nominal tape capacity in terabytes — used by the simulator only "
            "(default: 18)."
        ),
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
    parser.add_argument(
        "--report-dir",
        metavar="DIR",
        help="Write an HTML restore report to this directory after restore.",
    )
    parser.add_argument(
        "--detail",
        choices=[DETAIL_CONTAINER, DETAIL_FILE],
        default=DETAIL_CONTAINER,
        help=(
            "Detail level for the restore report: "
            f"'{DETAIL_CONTAINER}' (default) shows per-container SHA-256 verification; "
            f"'{DETAIL_FILE}' shows per-file restore status."
        ),
    )

    args = parser.parse_args()

    if args.device and not args.mount_point:
        parser.error("--mount-point is required when --device is used.")

    if not args.catalog and not args.first_tape_id:
        parser.error(
            "--first-tape-id is required when --catalog is not given "
            "(needed to load the catalog from tape)."
        )

    LoggingConfig(verbose=args.verbose).configure()

    tapes_root = Path(args.simulator) if args.simulator else Path(args.mount_point)
    config = BackupConfig(
        source_root=tapes_root,  # not used by RestoreService; placeholder
        tapes_root=tapes_root,
        tape_nominal_capacity_bytes=int(args.capacity_tb * _BYTES_PER_TB),
        max_container_size_bytes=int(args.capacity_tb * _BYTES_PER_TB),
    )

    try:
        if args.simulator:
            service = build_restore_service(config)
        else:
            service = build_ltfs_restore_service(
                config,
                device=Path(args.device),
                mount_point=Path(args.mount_point),
            )

        if args.catalog:
            catalog_bytes = Path(args.catalog).read_bytes()
            catalog = JsonCatalogSerializer().deserialize(catalog_bytes)
            logger.info(
                "Catalog loaded from file %s: backup_set=%s",
                args.catalog,
                catalog.backup_set_id,
            )
        else:
            catalog = service.load_catalog_from_tape(args.first_tape_id)

        report = service.restore(
            catalog,
            restore_root=Path(args.restore_to),
            filter_glob=args.filter,
        )
    except BackupError as exc:
        logger.error("Restore failed: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Restore complete. "
        f"{report.files_restored}/{report.files_requested} file(s) restored."
    )

    if args.report_dir:
        report_path = RestoreReportService().generate(
            catalog,
            report,
            Path(args.restore_to),
            args.filter,
            Path(args.report_dir),
            detail_level=args.detail,
        )
        print(f"Report written to {report_path}")

    if report.errors:
        for err in report.errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
