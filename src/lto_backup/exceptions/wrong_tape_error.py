from lto_backup.exceptions.backup_error import BackupError


class WrongTapeError(BackupError):
    """Raised when the tape loaded in the drive does not match the expected tape_id."""
