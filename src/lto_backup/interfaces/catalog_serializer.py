from typing import Protocol

from lto_backup.domain.catalog import Catalog


class CatalogSerializer(Protocol):
    def serialize(self, catalog: Catalog) -> bytes: ...

    def deserialize(self, data: bytes) -> Catalog: ...
