"""BackupService — orchestrates scan, plan, write, and catalog operations."""

import logging

from lto_backup.config.backup_config import BackupConfig
from lto_backup.domain.catalog import Catalog
from lto_backup.services.backup_planner import BackupPlanner
from lto_backup.services.backup_writer import BackupWriter
from lto_backup.services.catalog_service import CatalogService
from lto_backup.services.source_scanner import SourceScanner

logger = logging.getLogger(__name__)


class BackupService:
    """Orchestrates a full backup: scan → plan → write → catalog."""

    def __init__(
        self,
        scanner: SourceScanner,
        planner: BackupPlanner,
        writer: BackupWriter,
        catalog_service: CatalogService,
    ) -> None:
        self._scanner = scanner
        self._planner = planner
        self._writer = writer
        self._catalog_service = catalog_service

    def run(self, config: BackupConfig) -> Catalog:
        """Execute a full backup and return the completed catalog."""
        logger.info("BackupService: starting backup from %s", config.source_root)

        source_files = self._scanner.scan(config.source_root)
        logger.info("BackupService: %d file(s) found", len(source_files))

        plan = self._planner.plan(source_files, config)
        logger.info("BackupService: plan requires %d tape(s)", len(plan.tapes))

        sha256_map = self._writer.compute_sha256s(plan)
        catalog = self._catalog_service.build_catalog(plan, sha256_map)

        self._writer.write(
            plan,
            post_tape_callback=lambda td: self._catalog_service.write_catalog_to_tape(
                catalog, td
            ),
        )

        logger.info(
            "BackupService: catalog written to %d tape(s)", len(plan.tapes)
        )
        return catalog
