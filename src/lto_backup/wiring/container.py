"""Composition root — wires all concrete classes into a BackupService."""

from pathlib import Path

from lto_backup.config.backup_config import BackupConfig
from lto_backup.infrastructure.catalog.json_catalog_serializer import JsonCatalogSerializer
from lto_backup.infrastructure.clock.system_clock import SystemClock
from lto_backup.infrastructure.filesystem.local_file_system import LocalFileSystem
from lto_backup.infrastructure.filesystem.sha256_file_hasher import Sha256FileHasher
from lto_backup.infrastructure.simulator.simulator_tape_drive import SimulatorTapeDrive
from lto_backup.infrastructure.tape.linux_lto_tape_drive import LinuxLtoTapeDrive
from lto_backup.interfaces.tape_drive import TapeDrive
from lto_backup.interfaces.user_prompt import UserPrompt
from lto_backup.services.backup_planner import BackupPlanner
from lto_backup.services.backup_service import BackupService
from lto_backup.services.backup_writer import BackupWriter
from lto_backup.services.catalog_service import CatalogService
from lto_backup.services.source_scanner import SourceScanner
from lto_backup.services.tape_switch_service import TapeSwitchService


class StdinUserPrompt:
    """Concrete UserPrompt that reads from stdin and writes to stdout."""

    def ask(self, message: str) -> str:
        return input(message)

    def inform(self, message: str) -> None:
        print(message)  # noqa: T201 — intentional user-facing output in CLI adapter


def _build_backup_service_with_drive(config: BackupConfig, tape_drive: TapeDrive) -> BackupService:
    file_system = LocalFileSystem()
    file_hasher = Sha256FileHasher()
    clock = SystemClock()
    serializer = JsonCatalogSerializer()

    scanner = SourceScanner(file_system, file_hasher, clock)
    planner = BackupPlanner(serializer, clock)
    writer = BackupWriter(tape_drive, file_system, file_hasher)
    catalog_service = CatalogService(serializer, clock)

    return BackupService(scanner, planner, writer, catalog_service)


def build_backup_service(config: BackupConfig) -> BackupService:
    """Wire and return a BackupService backed by the simulator tape drive."""
    tape_drive = SimulatorTapeDrive(
        config.tapes_root,
        config.tape_nominal_capacity_bytes,
    )
    return _build_backup_service_with_drive(config, tape_drive)


def build_ltfs_backup_service(
    config: BackupConfig, device: Path, mount_point: Path
) -> BackupService:
    """Wire and return a BackupService backed by a real LTFS tape drive."""
    tape_drive = LinuxLtoTapeDrive(device, mount_point)
    return _build_backup_service_with_drive(config, tape_drive)


def build_tape_switch_service(tape_drive: TapeDrive) -> TapeSwitchService:
    """Wire and return a TapeSwitchService using stdin/stdout for operator interaction."""
    prompt: UserPrompt = StdinUserPrompt()
    return TapeSwitchService(tape_drive, prompt)

