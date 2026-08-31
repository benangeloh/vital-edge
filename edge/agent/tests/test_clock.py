"""Test penomoran sequence dan deteksi lompatan jam.

Ini komponen kecil yang menopang jaminan paling penting di sistem: sequence yang
menjadi patokan urutan, bukan jam. Kalau bagian ini salah, batas batch bisa rusak
dan duplikat jadi mustahil dideteksi.
"""

from __future__ import annotations

import pytest

from fleetview_edge.collector.clock import CollectorClock, InMemorySequenceSource


class TestSequenceSource:
    def test_mulai_dari_satu(self) -> None:
        assert InMemorySequenceSource().allocate(1) == 1

    def test_alokasi_berurutan_tanpa_celah(self) -> None:
        src = InMemorySequenceSource()
        assert src.allocate(3) == 1  # 1,2,3
        assert src.allocate(2) == 4  # 4,5
        assert src.current == 5

    def test_bisa_lanjut_dari_nomor_tertentu(self) -> None:
        """Phase 3 akan memulihkan counter dari SQLite setelah restart."""
        assert InMemorySequenceSource(start=1000).allocate(1) == 1001

    def test_count_nol_ditolak(self) -> None:
        with pytest.raises(ValueError, match="positif"):
            InMemorySequenceSource().allocate(0)


class TestCollectorClock:
    def test_stamp_batch_memberi_sequence_unik_dengan_timestamp_sama(self) -> None:
        """Semua pembacaan dalam satu putaran diambil pada momen yang sama secara
        logis, tapi urutannya tetap harus terdefinisi."""
        stamps = CollectorClock().stamp_batch(5)
        assert [s.sequence for s in stamps] == [1, 2, 3, 4, 5]
        assert len({s.timestamp_us for s in stamps}) == 1

    def test_sequence_terus_naik_antar_putaran(self) -> None:
        clock = CollectorClock()
        first = clock.stamp_batch(3)
        second = clock.stamp_batch(3)
        assert second[0].sequence == first[-1].sequence + 1

    def test_stamp_batch_nol_mengembalikan_kosong(self) -> None:
        assert CollectorClock().stamp_batch(0) == []

    def test_putaran_pertama_tidak_pernah_ditandai_lompat(self) -> None:
        """Tidak ada pembanding pada stamp pertama."""
        assert CollectorClock().stamp().clock_adjusted is False

    def test_jam_normal_tidak_menandai_lompatan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wall = [1_000_000_000_000_000]
        mono = [500_000.0]

        monkeypatch.setattr("fleetview_edge.collector.clock.now_micros", lambda: wall[0])
        monkeypatch.setattr("time.monotonic", lambda: mono[0])

        clock = CollectorClock()
        clock.stamp()
        # Kedua jam maju 1 detik bersama-sama.
        wall[0] += 1_000_000
        mono[0] += 1.0
        assert clock.stamp().clock_adjusted is False
        assert clock.jump_count == 0

    def test_jam_melompat_mundur_ditandai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Skenario nyata: NTP menyetel ulang jam begitu 4G tersambung setelah
        kapal berhari-hari offline dengan RTC yang melenceng."""
        wall = [1_000_000_000_000_000]
        mono = [500_000.0]

        monkeypatch.setattr("fleetview_edge.collector.clock.now_micros", lambda: wall[0])
        monkeypatch.setattr("time.monotonic", lambda: mono[0])

        clock = CollectorClock()
        clock.stamp()
        mono[0] += 1.0  # 1 detik nyata berlalu
        wall[0] -= 3_600_000_000  # jam dinding mundur satu jam

        stamp = clock.stamp()
        assert stamp.clock_adjusted is True
        assert clock.jump_count == 1

    def test_jam_melompat_maju_ditandai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wall = [1_000_000_000_000_000]
        mono = [500_000.0]
        monkeypatch.setattr("fleetview_edge.collector.clock.now_micros", lambda: wall[0])
        monkeypatch.setattr("time.monotonic", lambda: mono[0])

        clock = CollectorClock()
        clock.stamp()
        mono[0] += 1.0
        wall[0] += 3_600_000_000
        assert clock.stamp().clock_adjusted is True

    def test_sequence_tetap_monoton_meski_jam_mundur(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Inilah alasan seluruh mekanisme ini ada: urutan harus tetap terdefinisi
        justru ketika timestamp-nya tidak bisa dipercaya."""
        wall = [1_000_000_000_000_000]
        mono = [500_000.0]
        monkeypatch.setattr("fleetview_edge.collector.clock.now_micros", lambda: wall[0])
        monkeypatch.setattr("time.monotonic", lambda: mono[0])

        clock = CollectorClock()
        sequences = [clock.stamp().sequence]
        for _ in range(5):
            mono[0] += 1.0
            wall[0] -= 1_000_000_000  # terus mundur
            sequences.append(clock.stamp().sequence)

        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)

    def test_timestamp_tidak_dikoreksi_diam_diam(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lompatan ditandai, bukan diperbaiki. Mengoreksinya akan menghapus bukti
        bahwa jam kapal bermasalah — padahal itu yang perlu diketahui operator."""
        wall = [1_000_000_000_000_000]
        mono = [500_000.0]
        monkeypatch.setattr("fleetview_edge.collector.clock.now_micros", lambda: wall[0])
        monkeypatch.setattr("time.monotonic", lambda: mono[0])

        clock = CollectorClock()
        clock.stamp()
        mono[0] += 1.0
        wall[0] -= 3_600_000_000
        assert clock.stamp().timestamp_us == wall[0]

    def test_ambang_bisa_diatur(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wall = [1_000_000_000_000_000]
        mono = [500_000.0]
        monkeypatch.setattr("fleetview_edge.collector.clock.now_micros", lambda: wall[0])
        monkeypatch.setattr("time.monotonic", lambda: mono[0])

        clock = CollectorClock(jump_threshold_seconds=10.0)
        clock.stamp()
        mono[0] += 1.0
        wall[0] += 6_000_000  # penyimpangan 5 detik, di bawah ambang 10 detik
        assert clock.stamp().clock_adjusted is False
