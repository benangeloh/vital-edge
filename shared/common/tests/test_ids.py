"""Test pembuatan identifier."""

from __future__ import annotations

from fleetview_common import new_id, uuid7


def test_uuid7_menghasilkan_versi_7() -> None:
    assert uuid7().version == 7


def test_uuid7_unik() -> None:
    assert len({uuid7() for _ in range(1000)}) == 1000


def test_uuid7_terurut_waktu() -> None:
    """Sifat terurut ini yang menjaga index primary key ledger di central tetap
    rapi saat 70 kapal mengirim bersamaan."""
    ids = [uuid7() for _ in range(50)]
    assert [str(i) for i in ids] == sorted(str(i) for i in ids)


def test_new_id_adalah_alias_uuid7() -> None:
    assert new_id is uuid7
