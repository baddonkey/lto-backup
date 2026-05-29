from lto_backup.exceptions.backup_error import BackupError


class FileReadError(BackupError):
    """Raised when reading a file (from a tape or the filesystem) fails."""
