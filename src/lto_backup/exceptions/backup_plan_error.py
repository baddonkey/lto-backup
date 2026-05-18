from lto_backup.exceptions.backup_error import BackupError


class BackupPlanError(BackupError):
    """Raised when a backup plan cannot be created (e.g. invalid capacity)."""
