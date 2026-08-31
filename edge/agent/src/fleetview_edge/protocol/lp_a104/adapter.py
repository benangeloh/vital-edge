"""Adapter LP-A104 — KERANGKA. Belum bisa membaca perangkat sungguhan.

## Kenapa belum diimplementasikan

Manual sudah ada (151 halaman, ditambahkan 2026-08-31) dan sebagian isinya sudah
dipakai: model alamat device di `addressing.py` dan parameter komunikasi di
`serial_params.py` keduanya diambil langsung dari manual, lengkap dengan rujukan
bagian, dan diuji.

Yang **tidak ada di manual mana pun** adalah mekanisme bagi sistem luar untuk
membaca data dari LP-A104. Perangkat ini bukan protocol gateway — ia HMI dengan
PLC logic yang selalu berperan sebagai **master** yang mem-polling perangkat lain.
Tidak ada mode Modbus slave, tidak ada OPC-UA, MQTT, REST, FTP, maupun web server
yang terdokumentasi.

Karena itu register mapping dan protokol transport **tidak dikarang**. Nilai
yang dikarang akan menghasilkan data yang terlihat masuk akal tetapi salah — dan
untuk sistem monitoring, itu kegagalan terburuk, karena tidak menimbulkan gejala
apa pun sampai seseorang mengambil keputusan berdasarkan angka yang keliru.

Enam kandidat jalur integrasi beserta risikonya ada di `docs/hardware/LP-A104.md`.

## Sampai jalurnya diputuskan

Pakai `SimulatorAdapter` untuk pengembangan dan `MockLPAAdapter` untuk pengujian.
Keduanya memenuhi kontrak `ProtocolAdapter` yang sama, jadi begitu adapter
sungguhan siap, tidak ada kode di hulu yang perlu berubah.
"""

from __future__ import annotations

from fleetview_common import ProtocolError
from fleetview_edge.protocol.base import (
    AdapterHealth,
    LinkState,
    ProtocolAdapter,
    RawPoint,
)
from fleetview_edge.protocol.lp_a104.serial_params import SerialParams

__all__ = ["LPA104Adapter"]


class LPA104Adapter(ProtocolAdapter):
    """Kerangka adapter untuk Autonics LP-A104.

    Sengaja gagal keras saat dipakai. Adapter yang diam-diam mengembalikan daftar
    kosong akan tampak seperti kapal yang sensornya sedang mati — persis kondisi
    yang paling tidak boleh kita samarkan.
    """

    name = "lp_a104"

    def __init__(self, *, serial: SerialParams | None = None) -> None:
        # Parameter tetap divalidasi terhadap yang didukung perangkat, sehingga
        # config yang salah ketik ketahuan sekarang, bukan saat commissioning.
        self.serial = serial or SerialParams()
        self._link = LinkState.DISCONNECTED

    def _not_implemented(self, operation: str) -> ProtocolError:
        return ProtocolError(
            f"LPA104Adapter.{operation} belum diimplementasikan: jalur baca dari "
            f"LP-A104 belum terkonfirmasi. Lihat docs/hardware/LP-A104.md. "
            f"Gunakan SimulatorAdapter atau MockLPAAdapter untuk sementara.",
            code="lp_a104.not_implemented",
            retryable=False,
            details={"operation": operation},
        )

    async def connect(self) -> None:
        # TODO(hardware): butuh keputusan jalur integrasi lebih dulu.
        #   Pertanyaan yang menghambat, lihat docs/hardware/LP-A104.md:
        #     - sambungan fisik ke Pi: Ethernet, RS-485, atau RS-232?
        #     - protokolnya apa? (manual tidak mendokumentasikan mode slave)
        #     - bagaimana kerusakan sensor ditandai perangkat?
        raise self._not_implemented("connect")

    async def read_points(self) -> list[RawPoint]:
        # TODO(hardware): butuh register map dari project atDesigner/atLogic
        #   yang sedang berjalan di kapal. Alamat UW-nya sendiri sudah bisa
        #   dihitung — lihat addressing.can_module_range() — tetapi arti tiap
        #   alamat (sensor mana, skala berapa, satuan apa) ada di project itu.
        raise self._not_implemented("read_points")

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            link=self._link,
            detail="LPA104Adapter belum diimplementasikan; jalur integrasi belum terkonfirmasi",
        )

    async def close(self) -> None:
        self._link = LinkState.DISCONNECTED
