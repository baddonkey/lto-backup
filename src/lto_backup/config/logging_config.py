import logging
import sys


class LoggingConfig:
    """Configures the root logger for the lto-backup application.

    Call ``configure()`` once at startup (e.g. in the CLI entry point).
    """

    FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

    def __init__(self, *, verbose: bool = False) -> None:
        self._level = logging.DEBUG if verbose else logging.INFO

    def configure(self) -> None:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(self.FORMAT, datefmt=self.DATE_FORMAT))
        root = logging.getLogger()
        root.setLevel(self._level)
        root.addHandler(handler)
