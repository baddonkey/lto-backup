from lto_backup.exceptions.backup_error import BackupError


class CatalogWriteError(BackupError):
    """Raised when the catalog cannot be serialized or written to tape."""
