from dataclasses import dataclass


@dataclass(frozen=True)
class TapeSegment:
    segment_id: str
    file_id: str
    tape_id: str
    container_id: str
    source_offset: int
    length_bytes: int
    container_offset: int
    sha256: str
