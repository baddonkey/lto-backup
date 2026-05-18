from lto_backup.exceptions.backup_error import BackupError


class ContainerWriteError(BackupError):
    """Raised when a container file cannot be written to tape."""
