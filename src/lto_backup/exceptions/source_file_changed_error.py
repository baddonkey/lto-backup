from lto_backup.exceptions.backup_error import BackupError


class SourceFileChangedError(BackupError):
    """Raised when a source file is modified during the backup operation."""
