from dataclasses import dataclass


@dataclass(frozen=True)
class Container:
    container_id: str
    backup_set_id: str
    tape_id: str
    sequence_number: int  # global sequence within backup set
    tape_offset: int  # byte offset on tape where this container starts
    size_bytes: int  # actual bytes written into this container
