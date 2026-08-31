"""Test enkoder line protocol.

Aturan escaping adalah bagian yang paling mudah salah di seluruh jalur
penyimpanan, dan kesalahannya tidak menimbulkan error — InfluxDB hanya akan
menyimpan seri yang salah bentuk, dan itu baru ketahuan berminggu-minggu
kemudian saat ada yang mencoba mem-query-nya.
"""

from __future__ import annotations

from uuid import UUID

from fleetview_contracts import AcquisitionSource, Quality, TelemetryRecord
from fleetview_edge.storage import encode_record, encode_records
from fleetview_edge.storage.lineprotocol import escape_field_string, escape_tag

SHIP = UUID("11111111-1111-1111-1111-111111111111")
DEVICE = UUID("22222222-2222-2222-2222-222222222222")


def _record(**over: object) -> TelemetryRecord:
    base: dict[str, object] = {
        "ship_id": SHIP,
        "device_id": DEVICE,
        "sensor_id": "me_port_rpm",
        "sequence_number": 1,
        "timestamp": 1_756_612_800_000_000,
        "metric": "rpm",
        "values": {"value": 742.5},
        "unit": "rpm",
    }
    return TelemetryRecord(**{**base, **over})


class TestStruktur:
    def test_bentuk_dasar(self) -> None:
        line = encode_record(_record(tags={}))
        assert line.split(",", 1)[0] == "rpm"
        assert line.endswith(" 1756612800000000")

    def test_tag_identitas_selalu_ada(self) -> None:
        """ship_id, device_id, sensor_id adalah dimensi identitas — tanpa
        ketiganya, satu titik data tidak bisa dilacak asalnya."""
        line = encode_record(_record())
        assert f"ship_id={SHIP}" in line
        assert f"device_id={DEVICE}" in line
        assert "sensor_id=me_port_rpm" in line

    def test_timestamp_pakai_waktu_pengukuran_bukan_waktu_tulis(self) -> None:
        """Data hasil buffering saat InfluxDB sempat mati harus mendarat di
        posisi waktu yang benar, bukan menggumpal di detik saat sambungan pulih."""
        line = encode_record(_record(timestamp=1_600_000_000_000_000))
        assert line.endswith(" 1600000000000000")

    def test_tag_diurutkan_agar_hasilnya_stabil(self) -> None:
        a = encode_record(_record(tags={"engine": "port", "point": "exhaust"}))
        b = encode_record(_record(tags={"point": "exhaust", "engine": "port"}))
        assert a == b

    def test_quality_dan_source_jadi_tag(self) -> None:
        """Keduanya dipakai untuk memfilter — "kecualikan pembacaan suspect",
        "kecualikan data simulasi" — jadi harus jadi tag, bukan field."""
        line = encode_record(_record(quality=Quality.SUSPECT, source=AcquisitionSource.SIMULATED))
        assert "quality=suspect" in line
        assert "source=simulated" in line

    def test_unit_dihilangkan_bila_kosong(self) -> None:
        assert "unit=" not in encode_record(_record(unit=None))

    def test_tag_sensor_ikut_disertakan(self) -> None:
        line = encode_record(_record(tags={"engine": "port"}))
        assert "engine=port" in line

    def test_banyak_record_jadi_banyak_baris(self) -> None:
        payload = encode_records([_record(), _record(sensor_id="bus_v")])
        assert len(payload.splitlines()) == 2


class TestTipeField:
    def test_float(self) -> None:
        assert " value=742.5 " in encode_record(_record(values={"value": 742.5}))

    def test_integer_diberi_sufiks_i(self) -> None:
        """Tanpa sufiks `i`, InfluxDB menyimpannya sebagai float dan penghitung
        yang besar mulai kehilangan presisi."""
        assert " value=742i " in encode_record(_record(values={"value": 742}))

    def test_boolean(self) -> None:
        """Diperiksa sebelum int — bool adalah subclass int di Python, dan salah
        urutan akan menulis `1i` sehingga tipenya berubah jadi integer."""
        assert " value=true " in encode_record(_record(values={"value": True}))
        assert " value=false " in encode_record(_record(values={"value": False}))

    def test_string_diberi_kutip(self) -> None:
        assert ' value="ABC-123" ' in encode_record(_record(values={"value": "ABC-123"}))

    def test_multi_field_untuk_gps(self) -> None:
        line = encode_record(
            _record(metric="position", values={"lat": -7.25, "lon": 112.75}, unit=None)
        )
        assert "lat=-7.25" in line
        assert "lon=112.75" in line


class TestEscaping:
    def test_escape_tag_menangani_koma_spasi_samadengan(self) -> None:
        assert escape_tag("a,b") == r"a\,b"
        assert escape_tag("a b") == r"a\ b"
        assert escape_tag("a=b") == r"a\=b"

    def test_escape_string_field_menangani_kutip_dan_backslash(self) -> None:
        assert escape_field_string('say "hi"') == 'say \\"hi\\"'
        assert escape_field_string("a\\b") == "a\\\\b"

    def test_backslash_di_escape_lebih_dulu(self) -> None:
        r"""Kalau backslash tidak diproses pertama, escape berikutnya akan ikut
        ter-escape dan hasilnya `\\"` bukan `\"`."""
        assert escape_field_string('\\"') == '\\\\\\"'

    def test_nilai_tag_dengan_spasi_ter_escape(self) -> None:
        """Nilai tag berasal dari config sensor dan tidak divalidasi sebagai
        slug — jadi bisa saja mengandung spasi."""
        line = encode_record(_record(tags={"lokasi": "ruang mesin"}))
        assert r"lokasi=ruang\ mesin" in line

    def test_string_field_dengan_kutip_ter_escape(self) -> None:
        line = encode_record(_record(values={"value": 'kode "A1"'}))
        assert 'value="kode \\"A1\\""' in line
