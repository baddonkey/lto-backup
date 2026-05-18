from lto_backup.exceptions.backup_error import BackupError


class TapeNotLoadedError(BackupError):
    """Raised when a tape drive operation is attempted with no tape loaded."""
