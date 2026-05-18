from lto_backup.exceptions.backup_error import BackupError


class FileWriteError(BackupError):
    """Raised when a source file cannot be written to tape."""
