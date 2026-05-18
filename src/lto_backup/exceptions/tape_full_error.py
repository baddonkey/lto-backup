from lto_backup.exceptions.backup_error import BackupError


class TapeFullError(BackupError):
    """Raised when a write would exceed the tape's usable capacity."""
