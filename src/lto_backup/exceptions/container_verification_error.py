from lto_backup.exceptions.backup_error import BackupError


class ContainerVerificationError(BackupError):
    """Raised when a container's read-back SHA-256 does not match the value computed during write."""
