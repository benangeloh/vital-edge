"""Pengurai baris logger: satu record Flux menjadi satu baris tabel."""

from __future__ import annotations

from fleetview_central.modules.telemetry.service import parse_flux_rows, safe_sensor_id

_CSV = (
    "#datatype,string,long,dateTime:RFC3339,double,string,string,string,string,string\n"
    "#group,false,false,false,false,true,true,true,true,true\n"
    "#default,_result,,,,,,,,\n"
    ",result,table,_time,_value,_field,_measurement,sensor_id,unit,quality\n"
    ",,0,2026-10-21T05:00:02Z,1450.5,value,rpm,me_rpm,rpm,good\n"
    ",,0,2026-10-21T05:00:01Z,1449.0,value,rpm,me_rpm,rpm,suspect\n"
    "\n"
    "#datatype,string,long,dateTime:RFC3339,string,string,string,string\n"
    "#group,false,false,false,false,true,true,true\n"
    "#default,_result,,,,,,\n"
    ",result,table,_time,_value,_field,_measurement,sensor_id\n"
    ",,1,2026-10-21T05:00:03Z,ONLINE,state,link,gps_main\n"
)


class TestParseFluxRows:
    def test_satu_record_satu_baris(self) -> None:
        rows = parse_flux_rows(_CSV)
        assert len(rows) == 3

    def test_terurut_terbaru_dulu_lintas_blok(self) -> None:
        rows = parse_flux_rows(_CSV)
        assert [r["time"] for r in rows] == [
            "2026-10-21T05:00:03Z",
            "2026-10-21T05:00:02Z",
            "2026-10-21T05:00:01Z",
        ]

    def test_kolom_dan_tipe_nilai(self) -> None:
        first = next(r for r in parse_flux_rows(_CSV) if r["sensor_id"] == "me_rpm")
        assert first == {
            "time": "2026-10-21T05:00:02Z",
            "sensor_id": "me_rpm",
            "metric": "rpm",
            "field": "value",
            "value": 1450.5,
            "unit": "rpm",
            "quality": "good",
        }

    def test_nilai_teks_tetap_teks_dan_kolom_kosong_jadi_none(self) -> None:
        gps = next(r for r in parse_flux_rows(_CSV) if r["sensor_id"] == "gps_main")
        assert gps["value"] == "ONLINE"
        assert gps["unit"] is None
        assert gps["quality"] is None

    def test_kosong_bukan_error(self) -> None:
        assert parse_flux_rows("") == []
        assert parse_flux_rows("\r\n\r\n") == []


class TestSafeSensorId:
    def test_id_biasa_lolos(self) -> None:
        for ok in ("me_rpm", "sensor-1", "gps.main", "d100:uw1000"):
            assert safe_sensor_id(ok) == ok

    def test_yang_bisa_menyuntik_flux_ditolak(self) -> None:
        for bad in ('x") |> drop() //', 'a"b', "${env}", "a b", "", "x" * 65):
            assert safe_sensor_id(bad) is None
