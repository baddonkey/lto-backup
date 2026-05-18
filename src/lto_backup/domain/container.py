from dataclasses import dataclass, field


@dataclass(frozen=True)
class Container:
    container_id: str
    tape_id: str
    name: str
    size_bytes: int
    sha256: str
    segment_ids: list[str] = field(default_factory=list)
