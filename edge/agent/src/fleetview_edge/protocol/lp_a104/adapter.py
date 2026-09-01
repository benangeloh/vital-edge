"""Adapter Autonics LP-A104 lewat Modbus TCP.

## Jalur bacanya, dan dari mana kepastiannya datang

Manual LP-A104 tidak mendokumentasikan mekanisme apa pun bagi sistem luar untuk
membaca data — perangkat ini HMI dengan PLC logic yang berperan sebagai master
yang mem-polling perangkat lain. Karena itu adapter ini sempat sengaja dibiarkan
gagal keras, daripada register mapping-nya dikarang.

Yang mengubahnya bukan dokumen, melainkan **pengujian lapangan terhadap panel
sungguhan** (2026-08-21, panel 192.168.100.101). Kemampuan itu ternyata datang
dari konfigurasi proyek panelnya, bukan dari manual: kanal CH2 `UNIVERSAL
(AUTONICS)` disetel sebagai **Modbus TCP Slave** di port 502. Jadi panel memang
membuka port dan menjawab permintaan Modbus — tetapi hanya karena seseorang
mengonfigurasinya begitu di atDesigner.

## Yang harus dipahami sebelum memakai adapter ini

**Nilai sensor tidak otomatis terbaca dari Ethernet.** Sensor masuk ke panel
lewat CH1 (RS-485) dan mendarat di area `D` milik PLC — mis. `D00160` RPM,
`D00108` suhu air tawar, `D00110` tekanan pelumas. Yang diekspos ke Ethernet
adalah area `UW`, dan keduanya **memori yang berbeda** meski penamaannya mirip.

Pemetaannya sudah dipastikan: `UW000nn` ↔ register Modbus nomor `nn`, dibuktikan
lewat rung 26 ladder (`MOV H0002 UW00020`) yang memang terbaca sebagai 2 di
register 20.

Karena itu tiap sensor menuntut satu baris salin di ladder atLogic:

    MOV  D00160  UW00050        ; RPM        -> register Modbus 50
    MOV  D00108  UW00051        ; F.W Temp   -> register Modbus 51
    MOV  D00110  UW00052        ; L.O Press  -> register Modbus 52

Baris itu **tidak bisa dibuat dari FleetView** — ia ada di dalam program panel
dan perlu di-download lewat atLogic. Edge Console menampilkan baris yang
dibutuhkan untuk tiap sensor, sehingga teknisi tinggal menyalinnya, bukan
menebak.

## Nilai berkoma

Register Modbus hanya memuat bilangan bulat 16-bit; protokolnya tidak punya
koma. Nilai berkoma dikirim sebagai bilangan bulat dengan desimal tersirat, dan
objek Numeric Display di HMI yang memasang titik desimalnya. Contoh terverifikasi:
tekanan pelumas terbaca `31` dari Modbus dan tampil `3.1 bar` di HMI.

Penskalaan itu urusan registry sensor (`scale`/`offset`), bukan adapter. Adapter
tetap bodoh dan mengembalikan nilai mentah apa adanya.
"""

from __future__ import annotations

from fleetview_common import ProtocolError, ValidationError, get_logger, now_micros
from fleetview_edge.protocol.base import (
    AdapterHealth,
    LinkState,
    ProtocolAdapter,
    RawPoint,
)
from fleetview_edge.protocol.lp_a104.addressing import uw_area_of
from fleetview_edge.protocol.lp_a104.modbus import (
    MAX_REGISTERS_PER_REQUEST,
    ModbusTcpClient,
)

__all__ = ["LPA104Adapter", "parse_uw_channel"]

log = get_logger(__name__)


def parse_uw_channel(channel: str) -> int:
    """Ubah channel `UW50` menjadi nomor register Modbus 50.

    Bentuk `UW` dipertahankan sebagai penamaan channel karena itulah yang
    dilihat teknisi di atLogic; nomor register Modbus hanyalah bagaimana
    alamat itu muncul di sisi Ethernet.
    """
    teks = channel.strip().upper()
    if not teks.startswith("UW"):
        raise ProtocolError(
            f"channel {channel!r} bukan alamat UW; LP-A104 memakai bentuk UW, mis. UW50",
            code="lp_a104.channel_invalid",
            retryable=False,
        )
    digit = teks[2:]
    if not digit.isdigit():
        raise ProtocolError(
            f"channel {channel!r} tidak bisa diurai",
            code="lp_a104.channel_invalid",
            retryable=False,
        )
    alamat = int(digit)
    try:
        # Memvalidasi terhadap rentang terdokumentasi manual (UW0..UW128999).
        uw_area_of(alamat)
    except ValidationError as exc:
        # Diterjemahkan ke ProtocolError supaya kontrak adapter tetap satu jenis:
        # pemanggil tidak perlu tahu modul mana yang menolak alamatnya.
        raise ProtocolError(exc.message, code="lp_a104.channel_invalid", retryable=False) from exc
    return alamat


class LPA104Adapter(ProtocolAdapter):
    """Membaca register panel lewat Modbus TCP.

    Channel yang dibaca ditentukan registry sensor, bukan ditanam di sini.
    Adapter hanya tahu alamat; arti, satuan, dan skalanya urusan konfigurasi.
    """

    name = "lp_a104"

    def __init__(
        self,
        *,
        host: str,
        port: int = 502,
        channels: list[str] | None = None,
        unit_id: int = 1,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not host:
            raise ProtocolError(
                "alamat panel LP-A104 belum disetel (collector.lp_a104_host)",
                code="lp_a104.host_missing",
                retryable=False,
            )
        self._client = ModbusTcpClient(
            host=host, port=port, unit_id=unit_id, timeout_seconds=timeout_seconds
        )
        self._host = host
        self._channels = list(channels or [])
        self._alamat = {ch: parse_uw_channel(ch) for ch in self._channels}
        self._link = LinkState.DISCONNECTED
        self._connected_since_us: int | None = None
        self._last_success_us: int | None = None
        self._failures = 0
        self._counters = {"terbaca": 0, "gagal": 0, "permintaan": 0}

    async def connect(self) -> None:
        self._link = LinkState.CONNECTING
        try:
            await self._client.connect()
        except ProtocolError:
            # DISCONNECTED, bukan DEGRADED: sambungan tidak pernah terbentuk.
            self._link = LinkState.DISCONNECTED
            self._failures += 1
            raise
        self._link = LinkState.CONNECTED
        self._connected_since_us = now_micros()
        self._failures = 0

    async def read_points(self) -> list[RawPoint]:
        """Baca semua channel yang terdaftar, dalam sesedikit mungkin permintaan.

        Register yang berdekatan digabung menjadi satu permintaan blok. Ini bukan
        optimasi kosmetik: panel diperbarui dari RS-485 dengan Group Interval-nya
        sendiri, dan setiap permintaan tambahan hanya menambah beban tanpa
        menghasilkan data baru.
        """
        if not self._channels:
            # Tidak ada sensor terdaftar. Bukan kegagalan — tetapi juga tidak
            # boleh dilaporkan sebagai pembacaan sukses, karena kapal yang
            # registry-nya kosong akan terlihat persis seperti kapal yang sehat.
            raise ProtocolError(
                "tidak ada channel LP-A104 yang terdaftar di registry sensor",
                code="lp_a104.no_channels",
                retryable=False,
            )

        alamat_urut = sorted(set(self._alamat.values()))
        blok = _kelompokkan(alamat_urut)
        nilai: dict[int, int] = {}
        try:
            for mulai, jumlah in blok:
                self._counters["permintaan"] += 1
                hasil = await self._client.read_holding(mulai, jumlah)
                nilai.update({mulai + i: v for i, v in enumerate(hasil)})
        except ProtocolError:
            self._failures += 1
            self._counters["gagal"] += 1
            # DEGRADED: sambungannya ada, pembacaannya yang gagal. Bedanya
            # menentukan tindakan — yang satu periksa jaringan, yang satu
            # periksa panel.
            self._link = LinkState.DEGRADED
            raise

        saat = now_micros()
        self._last_success_us = saat
        self._failures = 0
        self._link = LinkState.CONNECTED
        titik = [
            RawPoint(channel=ch, value=nilai[self._alamat[ch]], read_at_us=saat)
            for ch in self._channels
            if self._alamat[ch] in nilai
        ]
        self._counters["terbaca"] += len(titik)
        return titik

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            link=self._link,
            connected_since_us=self._connected_since_us,
            last_success_us=self._last_success_us,
            consecutive_failures=self._failures,
            detail=f"Modbus TCP {self._host}, {len(self._channels)} channel",
            counters=dict(self._counters),
        )

    async def close(self) -> None:
        await self._client.close()
        self._link = LinkState.DISCONNECTED


def _kelompokkan(alamat: list[int], *, celah_maks: int = 8) -> list[tuple[int, int]]:
    """Gabungkan alamat berdekatan menjadi blok baca.

    Celah kecil ikut dibaca karena satu permintaan berisi beberapa register
    tambahan jauh lebih murah daripada satu permintaan terpisah — dan panel ini
    lebih sensitif pada JUMLAH permintaan daripada pada ukurannya.

    Blok tidak pernah melampaui batas aman panel; melebihinya menggantungkan
    stack Modbus-nya.
    """
    if not alamat:
        return []
    blok: list[tuple[int, int]] = []
    mulai = sebelum = alamat[0]
    for a in alamat[1:]:
        terlalu_jauh = a - sebelum > celah_maks
        terlalu_besar = a - mulai + 1 > MAX_REGISTERS_PER_REQUEST
        if terlalu_jauh or terlalu_besar:
            blok.append((mulai, sebelum - mulai + 1))
            mulai = a
        sebelum = a
    blok.append((mulai, sebelum - mulai + 1))
    return blok
