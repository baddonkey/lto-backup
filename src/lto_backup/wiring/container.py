"""Composition root — wires all concrete classes into a BackupService."""

from pathlib import Path

from lto_backup.config.backup_config import BackupConfig
from lto_backup.infrastructure.catalog.json_catalog_serializer import JsonCatalogSerializer
from lto_backup.infrastructure.clock.system_clock import SystemClock
from lto_backup.infrastructure.filesystem.local_file_system import LocalFileSystem
from lto_backup.infrastructure.filesystem.retry_file_system import RetryFileSystem
from lto_backup.infrastructure.filesystem.sha256_file_hasher import Sha256FileHasher
from lto_backup.infrastructure.simulator.simulator_tape_drive import SimulatorTapeDrive
from lto_backup.infrastructure.tape.linux_lto_tape_drive import LinuxLtoTapeDrive
from lto_backup.interfaces.file_system import FileSystem
from lto_backup.interfaces.tape_drive import TapeDrive
from lto_backup.interfaces.user_prompt import UserPrompt
from lto_backup.services.backup_planner import BackupPlanner
from lto_backup.services.backup_service import BackupService
from lto_backup.services.backup_writer import BackupWriter
from lto_backup.services.catalog_service import CatalogService
from lto_backup.services.restore_service import RestoreService
from lto_backup.services.source_scanner import SourceScanner
from lto_backup.services.tape_switch_service import TapeSwitchService
from lto_backup.services.verification_service import VerificationService


class StdinUserPrompt:
    """Concrete UserPrompt that reads from stdin and writes to stdout."""

    def ask(self, message: str) -> str:
        return input(message)

    def inform(self, message: str) -> None:
        print(message)  # noqa: T201 — intentional user-facing output in CLI adapter


class AutoconfirmUserPrompt:
    """UserPrompt that auto-confirms every ask() — used in simulator mode."""

    def ask(self, message: str) -> str:
        return ""

    def inform(self, message: str) -> None:
        print(message)  # noqa: T201 — intentional user-facing output in CLI adapter


def _make_file_system(config: BackupConfig) -> FileSystem:
    """Return LocalFileSystem, wrapped in RetryFileSystem when retries are configured."""
    local: FileSystem = LocalFileSystem()
    if config.read_retry_attempts > 1:
        return RetryFileSystem(
            local,
            max_attempts=config.read_retry_attempts,
            delay_seconds=config.read_retry_delay_seconds,
        )
    return local


def _build_backup_service_with_drive(
    config: BackupConfig, tape_drive: TapeDrive, autoconfirm: bool = False
) -> BackupService:
    file_system = _make_file_system(config)
    file_hasher = Sha256FileHasher()
    clock = SystemClock()
    serializer = JsonCatalogSerializer()
    tape_switch = (
        build_autoconfirm_tape_switch_service(tape_drive)
        if autoconfirm
        else build_tape_switch_service(tape_drive)
    )

    scanner = SourceScanner(file_system, file_hasher, clock)
    planner = BackupPlanner(serializer, clock)
    writer = BackupWriter(tape_drive, file_system, file_hasher, tape_switch)
    catalog_service = CatalogService(serializer, clock)

    return BackupService(scanner, planner, writer, catalog_service)


def build_backup_service(config: BackupConfig) -> BackupService:
    """Wire and return a BackupService backed by the simulator tape drive."""
    tape_drive = SimulatorTapeDrive(
        config.tapes_root,
        config.tape_nominal_capacity_bytes,
    )
    return _build_backup_service_with_drive(config, tape_drive, autoconfirm=True)


def build_ltfs_backup_service(
    config: BackupConfig, device: Path, mount_point: Path, mt_device: Path | None = None
) -> BackupService:
    """Wire and return a BackupService backed by a real LTFS tape drive."""
    tape_drive = LinuxLtoTapeDrive(device, mount_point, mt_device=mt_device)
    return _build_backup_service_with_drive(config, tape_drive, autoconfirm=False)


def build_tape_switch_service(tape_drive: TapeDrive) -> TapeSwitchService:
    """Wire and return a TapeSwitchService using stdin/stdout for operator interaction."""
    prompt: UserPrompt = StdinUserPrompt()
    return TapeSwitchService(tape_drive, prompt)


def build_autoconfirm_tape_switch_service(tape_drive: TapeDrive) -> TapeSwitchService:
    """Wire and return a TapeSwitchService that auto-confirms tape changes (simulator only)."""
    prompt: UserPrompt = AutoconfirmUserPrompt()
    return TapeSwitchService(tape_drive, prompt)


def _build_verification_service_with_drive(
    tape_drive: TapeDrive, autoconfirm: bool = False
) -> VerificationService:
    serializer = JsonCatalogSerializer()
    file_hasher = Sha256FileHasher()
    tape_switch = (
        build_autoconfirm_tape_switch_service(tape_drive)
        if autoconfirm
        else build_tape_switch_service(tape_drive)
    )
    return VerificationService(tape_drive, serializer, file_hasher, tape_switch)


def build_verification_service(config: BackupConfig) -> VerificationService:
    """Wire and return a VerificationService backed by the simulator tape drive."""
    tape_drive = SimulatorTapeDrive(
        config.tapes_root,
        config.tape_nominal_capacity_bytes,
    )
    return _build_verification_service_with_drive(tape_drive, autoconfirm=True)


def build_ltfs_verification_service(
    config: BackupConfig, device: Path, mount_point: Path, mt_device: Path | None = None
) -> VerificationService:
    """Wire and return a VerificationService backed by a real LTFS tape drive."""
    tape_drive = LinuxLtoTapeDrive(device, mount_point, mt_device=mt_device)
    return _build_verification_service_with_drive(tape_drive, autoconfirm=False)


def _build_restore_service_with_drive(
    config: BackupConfig, tape_drive: TapeDrive, autoconfirm: bool = False
) -> RestoreService:
    serializer = JsonCatalogSerializer()
    file_hasher = Sha256FileHasher()
    file_system = _make_file_system(config)
    tape_switch = (
        build_autoconfirm_tape_switch_service(tape_drive)
        if autoconfirm
        else build_tape_switch_service(tape_drive)
    )
    return RestoreService(tape_drive, tape_switch, serializer, file_hasher, file_system)


def build_restore_service(config: BackupConfig) -> RestoreService:
    """Wire and return a RestoreService backed by the simulator tape drive.

    Tape changes are auto-confirmed — no operator prompt is needed in simulator mode.
    """
    tape_drive = SimulatorTapeDrive(
        config.tapes_root,
        config.tape_nominal_capacity_bytes,
    )
    return _build_restore_service_with_drive(config, tape_drive, autoconfirm=True)


def build_ltfs_restore_service(
    config: BackupConfig, device: Path, mount_point: Path, mt_device: Path | None = None
) -> RestoreService:
    """Wire and return a RestoreService backed by a real LTFS tape drive."""
    tape_drive = LinuxLtoTapeDrive(device, mount_point, mt_device=mt_device)
    return _build_restore_service_with_drive(config, tape_drive)

