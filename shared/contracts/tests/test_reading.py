"""Test contract Reading.

Yang diuji di sini adalah aturan yang menjaga sistem tetap sehat di produksi:
disiplin slug (mencegah ledakan kardinalitas InfluxDB), fleksibilitas tipe nilai
(karena LP-A104 punya input digital dan counter), serta perlindungan tag sistem.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fleetview_contracts import AcquisitionSource, FieldValue, Quality, Reading


def test_scalar_helper_membungkus_nilai_ke_field_value() -> None:
    r = Reading.scalar(
        seq=1,
        ts=1_700_000_000_000_000,
        sensor_id="me_port_rpm",
        measurement="rpm",
        value=742.5,
        unit="rpm",
    )
    assert r.fields == {"value": 742.5}
    assert r.quality is Quality.GOOD
    assert r.source is AcquisitionSource.LIVE


def test_gps_dikirim_sebagai_satu_titik_multi_field() -> None:
    """lat dan lon harus berada di satu titik yang sama.

    Kalau dipecah jadi titik terpisah, salah satunya bisa hilang dan
    menghasilkan koordinat yang tidak konsisten.
    """
    r = Reading(
        seq=2,
        ts=1_700_000_000_000_000,
        sensor_id="gps_main",
        measurement="position",
        fields={"lat": -7.2575, "lon": 112.7521, "sog": 12.4, "cog": 87.0},
    )
    assert set(r.fields) == {"lat", "lon", "sog", "cog"}


@pytest.mark.parametrize("value", [742.5, 742, True, "ABC-123"])
def test_field_menerima_float_int_bool_dan_str(value: FieldValue) -> None:
    """LP-A104 punya 32 input digital, fungsi counter, dan bisa tersambung
    barcode reader. Mengunci ke float akan memaksa perubahan schema nanti."""
    r = Reading(seq=1, ts=1, sensor_id="s", measurement="m", fields={"value": value})
    assert r.fields["value"] == value


@pytest.mark.parametrize("bad", ["ME_PORT_RPM", "me-port-rpm", "1nvalid!", "", "x" * 65])
def test_sensor_id_menolak_slug_tidak_valid(bad: str) -> None:
    with pytest.raises(ValidationError):
        Reading.scalar(seq=1, ts=1, sensor_id=bad, measurement="rpm", value=1.0)


def test_measurement_menerima_slug_baru_di_luar_daftar_yang_dikenal() -> None:
    """Sensor bisa bertambah di masa depan tanpa perubahan kode."""
    r = Reading.scalar(seq=1, ts=1, sensor_id="s", measurement="shaft_torque", value=1.0)
    assert r.measurement == "shaft_torque"


def test_nama_tag_juga_divalidasi() -> None:
    with pytest.raises(ValidationError):
        Reading.scalar(
            seq=1, ts=1, sensor_id="s", measurement="m", value=1.0, tags={"Engine Side": "port"}
        )


@pytest.mark.parametrize("reserved", ["ship_id", "device_id", "sensor_id"])
def test_tag_milik_sistem_ditolak(reserved: str) -> None:
    """Tag ini ditambahkan saat penulisan ke InfluxDB dari batch envelope.
    Membiarkannya diset di Reading membuka peluang kontradiksi."""
    with pytest.raises(ValidationError):
        Reading.scalar(
            seq=1, ts=1, sensor_id="s", measurement="m", value=1.0, tags={reserved: "palsu"}
        )


def test_fields_tidak_boleh_kosong() -> None:
    with pytest.raises(ValidationError):
        Reading(seq=1, ts=1, sensor_id="s", measurement="m", fields={})


def test_reading_bersifat_immutable() -> None:
    r = Reading.scalar(seq=1, ts=1, sensor_id="s", measurement="m", value=1.0)
    with pytest.raises(ValidationError):
        r.seq = 2  # type: ignore[misc]


def test_field_tak_dikenal_ditolak() -> None:
    with pytest.raises(ValidationError):
        Reading(seq=1, ts=1, sensor_id="s", measurement="m", fields={"value": 1.0}, typo_field="x")  # type: ignore[call-arg]
