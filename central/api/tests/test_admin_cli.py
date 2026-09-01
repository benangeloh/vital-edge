"""Test CLI administrasi armada.

CLI ini adalah satu-satunya jalan menerbitkan kredensial device dan membuat
operator pertama. Dua prosedur operasional bergantung padanya —
docs/operations/07-ship-onboarding.md dan 08-ship-decommissioning.md — jadi
argumen dan perilakunya ikut menjadi kontrak, bukan detail implementasi.
"""

from __future__ import annotations

import pytest

from fleetview_central.admin import _build_parser

pytestmark = pytest.mark.integration


class TestPermukaanPerintah:
    """Perintah yang dirujuk runbook harus benar-benar ada dan menerima argumen
    yang tertulis di sana. Runbook yang perintahnya salah ketik membuang waktu
    teknisi tepat pada saat paling buruk."""

    @pytest.mark.parametrize(
        "argv",
        [
            ["ship", "add", "--name", "KM X", "--slug", "SHIP-071"],
            ["ship", "add", "--name", "KM X", "--slug", "SHIP-071", "--imo", "9123456"],
            [
                "ship",
                "add",
                "--name",
                "KM X",
                "--slug",
                "S",
                "--id",
                "11111111-1111-1111-1111-111111111111",
            ],
            ["ship", "list"],
            ["ship", "decommission", "11111111-1111-1111-1111-111111111111"],
            ["device", "add", "--ship-id", "abc", "--name", "Edge Pi #1"],
            ["device", "add", "--ship-id", "abc", "--name", "P", "--hardware", "Pi 4B"],
            ["credential", "issue", "--device-id", "abc", "--client-id", "ship-071"],
            ["credential", "revoke", "ship-071"],
            ["credential", "list"],
            ["user", "add", "--username", "operator"],
            ["user", "add", "--username", "operator", "--roles", "admin", "viewer"],
            ["user", "list"],
        ],
    )
    def test_perintah_runbook_bisa_diurai(self, argv: list[str]) -> None:
        args = _build_parser().parse_args(argv)
        assert callable(args.fn)

    @pytest.mark.parametrize(
        "argv",
        [
            ["ship", "add", "--name", "KM X"],  # slug wajib
            ["credential", "issue", "--client-id", "x"],  # device-id wajib
            ["user", "add"],  # username wajib
            ["ship"],  # subperintah wajib
        ],
    )
    def test_argumen_kurang_ditolak(self, argv: list[str]) -> None:
        with pytest.raises(SystemExit):
            _build_parser().parse_args(argv)

    def test_password_default_diminta_interaktif(self) -> None:
        """Kata sandi lewat argumen akan tersimpan di riwayat shell dan terlihat
        di daftar proses. Default-nya harus meminta secara interaktif."""
        args = _build_parser().parse_args(["user", "add", "--username", "operator"])
        assert args.password is None

    def test_peran_default_admin(self) -> None:
        args = _build_parser().parse_args(["user", "add", "--username", "operator"])
        assert args.roles == ["admin"]
