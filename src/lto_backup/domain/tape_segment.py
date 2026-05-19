from dataclasses import dataclass


@dataclass(frozen=True)
class TapeSegment:
    segment_id: str
    file_id: str
    container_id: str  # which container holds this segment
    container_offset: int  # byte offset within the container
    source_offset: int  # byte offset within the source file
    length_bytes: int
    sha256: str  # hash of these specific bytes; "" until written
