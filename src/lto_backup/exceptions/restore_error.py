from lto_backup.exceptions.backup_error import BackupError


class RestoreError(BackupError):
    """Raised when a restore operation fails."""
