from dataclasses import dataclass


@dataclass(frozen=True)
class TapeSegment:
    segment_id: str
    file_id: str
    tape_id: str
    tape_offset: int
    source_offset: int
    length_bytes: int
    sha256: str
