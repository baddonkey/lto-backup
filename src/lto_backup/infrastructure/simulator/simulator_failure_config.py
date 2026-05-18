from dataclasses import dataclass, field


@dataclass
class SimulatorFailureConfig:
    """Optional failure injection configuration for the simulator tape drive."""

    fail_on_write: bool = False
    fail_on_read: bool = False
    fail_on_load: bool = False
    fail_after_bytes_written: int | None = None
    error_message: str = "Simulated failure"
    failed_tape_ids: set[str] = field(default_factory=set)
