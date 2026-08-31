"""Test pemulihan sambungan.

Sambungan yang putus bukan kejadian luar biasa di kapal — getaran melonggarkan
konektor dan listrik mati-nyala. Yang diuji di sini adalah bahwa pemulihannya
tidak pernah menyerah dan tidak pernah melambat tanpa batas.
"""

from __future__ import annotations

from fleetview_edge.collector.supervisor import BackoffPolicy, ConnectionSupervisor
from fleetview_edge.protocol import MockLPAAdapter, MockScript

NO_WAIT = BackoffPolicy(initial_seconds=0.0, max_seconds=0.0, jitter=0.0)


class TestBackoffPolicy:
    def test_percobaan_pertama_tanpa_jeda(self) -> None:
        assert BackoffPolicy().delay_for(0) == 0.0

    def test_jeda_meningkat_secara_eksponensial(self) -> None:
        p = BackoffPolicy(initial_seconds=1.0, multiplier=2.0, jitter=0.0)
        assert [p.delay_for(i) for i in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]

    def test_jeda_berhenti_naik_di_batas_atas(self) -> None:
        """Ini penting secara operasional: sambungan yang pulih setelah enam jam
        harus terdeteksi dalam hitungan menit, bukan hari."""
        p = BackoffPolicy(initial_seconds=1.0, max_seconds=60.0, jitter=0.0)
        assert p.delay_for(20) == 60.0

    def test_jitter_menyebarkan_percobaan(self) -> None:
        """Tanpa jitter, 70 kapal yang putus bersamaan akan mencoba menyambung
        ulang pada detik yang sama berulang kali."""
        p = BackoffPolicy(initial_seconds=10.0, jitter=0.2, multiplier=1.0)
        delays = {p.delay_for(1) for _ in range(50)}
        assert len(delays) > 1
        assert all(8.0 <= d <= 12.0 for d in delays)

    def test_jeda_tidak_pernah_negatif(self) -> None:
        p = BackoffPolicy(initial_seconds=0.001, jitter=5.0)
        assert all(p.delay_for(i) >= 0.0 for i in range(1, 20))


class TestConnectionSupervisor:
    async def test_menyambung_saat_pertama_dipanggil(self) -> None:
        adapter = MockLPAAdapter({"a": 1.0})
        sup = ConnectionSupervisor(adapter, backoff=NO_WAIT)
        assert await sup.ensure_connected() is True
        assert adapter.is_connected is True

    async def test_tidak_menyambung_ulang_bila_sudah_tersambung(self) -> None:
        adapter = MockLPAAdapter({"a": 1.0})
        sup = ConnectionSupervisor(adapter, backoff=NO_WAIT)
        await sup.ensure_connected()
        await sup.ensure_connected()
        assert adapter.connect_attempts == 1

    async def test_satu_percobaan_per_panggilan(self) -> None:
        """Sengaja hanya satu percobaan, supaya loop collector tetap bisa
        dihentikan dengan rapi — loop yang mencoba selamanya di dalam satu
        panggilan tidak bisa dibatalkan."""
        adapter = MockLPAAdapter({"a": 1.0}, script=MockScript(connect_failures=5))
        sup = ConnectionSupervisor(adapter, backoff=NO_WAIT)
        assert await sup.ensure_connected() is False
        assert adapter.connect_attempts == 1

    async def test_pulih_setelah_beberapa_kegagalan(self) -> None:
        adapter = MockLPAAdapter({"a": 1.0}, script=MockScript(connect_failures=3))
        sup = ConnectionSupervisor(adapter, backoff=NO_WAIT)
        for _ in range(3):
            assert await sup.ensure_connected() is False
        assert await sup.ensure_connected() is True

    async def test_callback_reconnect_dipanggil_hanya_saat_menyambung_ulang(self) -> None:
        calls: list[int] = []
        adapter = MockLPAAdapter({"a": 1.0})
        sup = ConnectionSupervisor(adapter, backoff=NO_WAIT, on_reconnect=lambda: calls.append(1))

        await sup.ensure_connected()  # sambungan pertama
        assert calls == []

        sup.mark_disconnected("uji")
        await sup.ensure_connected()  # menyambung ulang
        assert calls == [1]

    async def test_mark_disconnected_memaksa_penyambungan_ulang(self) -> None:
        adapter = MockLPAAdapter({"a": 1.0})
        sup = ConnectionSupervisor(adapter, backoff=NO_WAIT)
        await sup.ensure_connected()
        sup.mark_disconnected("uji")
        assert sup.connected is False
        await sup.ensure_connected()
        assert adapter.connect_attempts == 2
