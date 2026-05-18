"""SystemClock — concrete Clock implementation using system time."""

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class SystemClock:
    """Returns the current UTC system time."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)
