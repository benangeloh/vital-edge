"""Konfigurasi pytest tingkat repo.

Satu-satunya tugasnya: memastikan integration test TIDAK PERNAH berjalan di
basis data yang dipakai sungguhan.

Test integrasi menjalankan `Base.metadata.drop_all` untuk memulai dari keadaan
bersih. Itu benar — selama sasarannya memang basis data uji. Yang tidak benar
adalah default-nya menunjuk `fleetview`, basis data yang sama dengan stack
pengembangan; sekali seseorang memakai stack itu sebagai central sungguhan
(misalnya untuk menguji Raspberry Pi di meja), `make check` akan menghapus
pendaftaran kapal dan kredensial perangkatnya tanpa peringatan apa pun.

Itu bukan hipotetis: sudah pernah terjadi.

Karena itu default-nya dipindahkan ke basis data terpisah `fleetview_test`, yang
dibuat otomatis bila belum ada.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

DEFAULT_SERVER = "postgresql+asyncpg://fleetview:fleetview@localhost:5432"
TEST_DB = "fleetview_test"


def _ensure_test_database(dsn: str) -> None:
    """Buat basis data uji bila belum ada. Diam saja kalau server tidak hidup —
    test-nya sendiri yang akan melewatkan diri lewat fixture `services`."""
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError:
        psycopg = None

    parsed = urlparse(dsn)
    name = parsed.path.lstrip("/")
    admin = urlunparse(parsed._replace(scheme="postgresql", path="/postgres"))

    if psycopg is not None:
        try:
            with psycopg.connect(admin, autocommit=True, connect_timeout=3) as conn:
                exists = conn.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
                ).fetchone()
                if not exists:
                    conn.execute(f'CREATE DATABASE "{name}"')
        except Exception:
            return
        return

    # Tanpa psycopg, coba lewat docker compose — jalur yang dipakai stack dev.
    import subprocess

    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            f"psql -U fleetview -tAc \"SELECT 1 FROM pg_database WHERE datname='{name}'\" "
            f"| grep -q 1 || createdb -U fleetview {name}",
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )


if "FLEETVIEW_TEST_POSTGRES_DSN" not in os.environ:
    os.environ["FLEETVIEW_TEST_POSTGRES_DSN"] = f"{DEFAULT_SERVER}/{TEST_DB}"

_ensure_test_database(os.environ["FLEETVIEW_TEST_POSTGRES_DSN"])
