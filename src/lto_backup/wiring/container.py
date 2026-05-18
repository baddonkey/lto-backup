"""Composition root — wires all concrete classes into a BackupService."""

from lto_backup.config.backup_config import BackupConfig
from lto_backup.infrastructure.catalog.json_catalog_serializer import JsonCatalogSerializer
from lto_backup.infrastructure.clock.system_clock import SystemClock
from lto_backup.infrastructure.filesystem.local_file_system import LocalFileSystem
from lto_backup.infrastructure.filesystem.sha256_file_hasher import Sha256FileHasher
from lto_backup.infrastructure.simulator.simulator_tape_drive import SimulatorTapeDrive
from lto_backup.services.backup_planner import BackupPlanner
from lto_backup.services.backup_service import BackupService
from lto_backup.services.backup_writer import BackupWriter
from lto_backup.services.catalog_service import CatalogService
from lto_backup.services.source_scanner import SourceScanner


def build_backup_service(config: BackupConfig) -> BackupService:
    """Wire and return a fully configured BackupService."""
    file_system = LocalFileSystem()
    file_hasher = Sha256FileHasher()
    clock = SystemClock()
    serializer = JsonCatalogSerializer()
    tape_drive = SimulatorTapeDrive(
        config.tapes_root,
        config.tape_nominal_capacity_bytes,
    )

    scanner = SourceScanner(file_system, file_hasher, clock)
    planner = BackupPlanner(serializer, clock)
    writer = BackupWriter(tape_drive, file_system, file_hasher)
    catalog_service = CatalogService(serializer, clock)

    return BackupService(scanner, planner, writer, catalog_service, tape_drive)

