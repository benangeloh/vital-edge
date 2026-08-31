"""Pembuatan identifier.

Seluruh sistem memakai UUIDv7 untuk id yang dibuat di edge. Sifat terurut-waktu
pada UUIDv7 penting secara operasional: `batch_id` menjadi primary key ledger
di central, dan id acak akan menyebarkan penulisan ke seluruh B-tree index saat
70 kapal mengirim bersamaan. UUIDv7 menjaga penulisan tetap terkumpul di ujung.
"""

from __future__ import annotations

from uuid import UUID

import uuid_utils

__all__ = ["new_id", "uuid7"]


def uuid7() -> UUID:
    """UUID versi 7 — terurut waktu, aman untuk dipakai sebagai primary key."""
    return UUID(str(uuid_utils.uuid7()))


new_id = uuid7
