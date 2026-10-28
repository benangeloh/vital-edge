"""SecretBox: enkripsi rahasia kredensial yang harus bisa dibaca kembali."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from fleetview_central.platform.secretbox import SecretBox
from fleetview_common import ConfigError, ValidationError


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


def test_bolak_balik_mengembalikan_nilai_asli(key: str) -> None:
    box = SecretBox(key)
    assert box.decrypt(box.encrypt("rahasia-kapal")) == "rahasia-kapal"


def test_hasil_enkripsi_tidak_memuat_teks_asli(key: str) -> None:
    assert "rahasia-kapal" not in SecretBox(key).encrypt("rahasia-kapal")


def test_tanpa_kunci_fitur_nonaktif() -> None:
    assert SecretBox(None).enabled is False
    assert SecretBox("").enabled is False


def test_dengan_kunci_fitur_aktif(key: str) -> None:
    assert SecretBox(key).enabled is True


def test_kunci_rusak_gagal_saat_start_bukan_saat_dipakai() -> None:
    with pytest.raises(ConfigError) as exc:
        SecretBox("bukan-kunci-fernet")
    assert exc.value.code == "config.invalid_credential_key"


def test_mengenkripsi_tanpa_kunci_ditolak() -> None:
    with pytest.raises(ConfigError):
        SecretBox(None).encrypt("x")


def test_kunci_berbeda_tidak_bisa_membuka(key: str) -> None:
    token = SecretBox(key).encrypt("rahasia-kapal")
    other = SecretBox(Fernet.generate_key().decode())
    with pytest.raises(ValidationError) as exc:
        other.decrypt(token)
    assert exc.value.code == "fleet.credential_unreadable"


def test_membuka_tanpa_kunci_ditolak(key: str) -> None:
    token = SecretBox(key).encrypt("rahasia-kapal")
    with pytest.raises(ValidationError) as exc:
        SecretBox(None).decrypt(token)
    assert exc.value.code == "fleet.credential_unreadable"
