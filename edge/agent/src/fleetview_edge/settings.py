"""Konfigurasi Edge Agent.

Config disusun berlapis, yang belakangan menimpa yang sebelumnya:

  1. default di dalam kode (di file ini)
  2. berkas YAML  (`--config` atau `FLEETVIEW_EDGE_CONFIG`)
  3. environment variable (berawalan `FLEETVIEW_`)

Lapisan keempat — config yang didorong dari central dan berversi — menyusul di
Phase 5. Itulah alasan `config_version` sudah ada sekarang: ia ikut di setiap
heartbeat dan setiap batch, sehingga saat suatu kapal berperilaku aneh, kita bisa
tahu persis konfigurasi mana yang sedang dijalankannya.

Satu build Edge Agent yang sama dideploy ke 70+ kapal. **Yang membedakan hanya
berkas config ini.** Karena itu identitas kapal wajib diisi dan tidak punya
default — agent menolak start daripada berjalan dengan identitas salah dan
mengirim data atas nama kapal lain.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from fleetview_common import ConfigError

__all__ = ["EdgeSettings", "load_settings"]


class ShipIdentity(BaseModel):
    """Identitas kapal.

    Seluruh isinya wajib bila bagian ini ada — kapal setengah teridentifikasi
    lebih berbahaya daripada kapal yang jelas-jelas belum dikonfigurasi. Bagian
    ini sendiri boleh tidak ada; lihat `EdgeSettings.ship`.
    """

    model_config = ConfigDict(extra="forbid")

    ship_id: UUID
    ship_name: str = Field(min_length=1, max_length=128)
    device_id: UUID


class StorageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_dir: Path = Path("/var/lib/fleetview")
    outbox_path: Path = Path("/var/lib/fleetview/outbox.db")
    """Outbox SQLite — source of truth untuk data yang belum disetor."""

    outbox_synchronous_full: bool = True
    """fsync di setiap commit. Inilah yang membuat data selamat dari mati
    listrik, dan itu satu-satunya alasan outbox ini ada. Matikan hanya untuk
    pengujian."""

    influx_url: str = "http://127.0.0.1:8086"
    influx_org: str = "fleetview"
    influx_username: str = "fleetview"
    """Nama pengguna admin InfluxDB lokal.

    Bukan dipakai agent — agent memakai token. Disimpan di sini semata agar Edge
    Console bisa memberitahukannya kepada teknisi yang perlu membuka UI InfluxDB
    di kapal untuk memastikan data benar-benar masuk.
    """
    influx_bucket: str = "telemetry"
    influx_token: str = ""
    """Kosong hanya boleh saat pengembangan. Diverifikasi ada di produksi."""

    retention_days: int = Field(default=90, ge=0)
    """Umur simpan telemetry di InfluxDB lokal. 0 berarti tak terbatas —
    tidak dianjurkan di edge, karena disk penuh menghentikan akuisisi."""

    influx_timeout_seconds: float = Field(default=10.0, gt=0)

    buffer_max_records: int = Field(default=10_000, gt=0)
    """Kapasitas buffer percobaan ulang in-memory.

    Buffer ini menangani InfluxDB yang mati sesaat, bukan mati listrik.
    Isinya hilang saat proses berakhir. Durabilitas sesungguhnya adalah tugas
    outbox di Phase 4. Pada 1 Hz dengan 80 sensor, 10.000 record kira-kira
    dua menit — cukup untuk restart atau compaction, tidak cukup untuk
    pemadaman panjang."""

    buffer_retry_batch_size: int = Field(default=500, gt=0)
    acked_grace_days: int = Field(default=7, ge=0)
    """Berapa lama baris yang sudah acked tetap disimpan. Menyisakan jendela
    replay lokal seandainya central pernah kehilangan data."""

    disk_warn_percent: float = Field(default=80.0, ge=0, le=100)
    disk_critical_percent: float = Field(default=90.0, ge=0, le=100)


class SyncSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    central_url: str = ""
    batch_max_records: int = Field(default=500, gt=0)
    batch_max_bytes: int = Field(default=1_048_576, gt=0)
    batch_max_age_seconds: int = Field(default=60, gt=0)
    heartbeat_interval_seconds: int = Field(default=60, gt=0)
    interval_seconds: float = Field(default=10.0, gt=0)

    device_client_id: str = ""
    device_secret: str = ""
    """Kredensial device jangka panjang, ditukar menjadi token saat dibutuhkan.

    **Ini yang dipakai di produksi.** Diterbitkan di central dengan
    `fleetview-admin credential issue`, lalu ditaruh di /etc/fleetview/secrets.env.

    Agent menukarnya sendiri dan menukar ulang otomatis saat token kedaluwarsa.
    Inilah yang membuat pencabutan kredensial berarti: begitu dicabut, penukaran
    berikutnya gagal dan kapal berhenti bisa mengirim.
    """

    device_token: str = ""
    """JWT device statis, sebagai alternatif kredensial di atas.

    Praktis untuk pengujian, tetapi **kedaluwarsa** (default satu jam di central).
    Kapal yang hanya dibekali ini akan berhenti menyetor data satu jam setelah
    dinyalakan. Jangan dipakai di kapal.
    """

    max_attempts: int = Field(default=10, gt=0)
    """Setelah sekian kegagalan berturut-turut, batch dikarantina. Datanya
    TIDAK dihapus — hanya berhenti dicoba supaya tidak menyumbat antrean."""

    acked_grace_days: int = Field(default=7, ge=0)
    """Berapa lama baris yang sudah acked disimpan setelah ACK diterima.
    Dihitung dari waktu ACK, bukan waktu pengukuran — kapal yang offline 30 hari
    lalu menyetor backlog-nya tetap punya jendela replay penuh."""

    retry_initial_seconds: float = Field(default=5.0, ge=0)
    retry_max_seconds: float = Field(default=1800.0, gt=0)

    cellular_monthly_budget_mb: int = Field(default=500, ge=0)
    cellular_max_priority: int = Field(default=1, ge=0, le=2)
    """Prioritas terendah yang boleh lewat seluler. Default 1 (SUMMARY):
    telemetry mentah 1 Hz tidak sepadan dengan kuota, alarm mesin sepadan."""


class CollectorSettings(BaseModel):
    """Setelan akuisisi data."""

    model_config = ConfigDict(extra="forbid")

    adapter: Literal["mock", "simulator", "lp_a104"] = "simulator"
    """Adapter perangkat lapangan.

    `lp_a104` membaca panel Autonics lewat Modbus TCP. Jalur itu sudah
    terkonfirmasi pada panel sungguhan; lihat docs/hardware/LP-A104.md.
    """

    lp_a104_host: str = ""
    """Alamat IP panel LP-A104 di jaringan kapal, mis. `192.168.100.101`."""

    lp_a104_port: int = Field(default=502, gt=0, lt=65536)

    lp_a104_unit_id: int = Field(default=1, ge=0, le=255)
    """Panel ini MENGABAIKAN unit id — 0, 1, 3, 255 dijawab sama. Tetap bisa
    disetel karena frame Modbus menuntutnya, tetapi mengubahnya tidak berefek."""

    sensors_path: Path | None = None
    """Berkas YAML konfigurasi sensor. Wajib diisi di produksi."""

    poll_interval_seconds: float = Field(default=1.0, gt=0)
    poll_timeout_seconds: float = Field(default=5.0, gt=0)
    clock_jump_threshold_seconds: float = Field(default=2.0, gt=0)

    backoff_initial_seconds: float = Field(default=1.0, ge=0)
    backoff_max_seconds: float = Field(default=60.0, gt=0)


class ConsoleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    host: str = "127.0.0.1"
    """Default hanya localhost. Ubah ke 0.0.0.0 secara sadar bila perlu diakses
    dari LAN kapal — Console tidak boleh menghadap internet."""
    port: int = Field(default=8080, gt=0, lt=65536)

    allow_lan: bool = False
    """Izinkan Console dijangkau dari jaringan kapal (`host: 0.0.0.0`).

    Console tidak punya autentikasi untuk halaman operasionalnya, jadi ini harus
    dinyatakan eksplisit — bukan tercapai diam-diam karena host kebetulan
    disetel 0.0.0.0.

    Dalam praktiknya ini memang dibutuhkan: teknisi membuka halaman setup dan
    diagnosis dari laptop atau tablet, dan mengharuskan port-forward SSH berarti
    mengembalikan terminal ke dalam alur yang justru ingin dibebaskan darinya.
    Halaman setup sendiri tetap dilindungi PIN.
    """


class EdgeSettings(BaseSettings):
    """Konfigurasi lengkap Edge Agent."""

    model_config = SettingsConfigDict(
        env_prefix="FLEETVIEW_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Urutan prioritas: environment > berkas YAML > default di kode.

        Urutan bawaan pydantic-settings menempatkan argumen init di atas
        environment. Di sini YAML dimuat sebagai argumen init, jadi urutan
        bawaannya akan membuat berkas mengalahkan environment — kebalikan dari
        yang dijanjikan docstring modul ini, dan kebalikan dari yang dibutuhkan
        operasional: rahasia dan penyesuaian per-kapal diberikan lewat env
        justru supaya bisa menimpa berkas config yang sudah ter-provision.
        """
        return (env_settings, dotenv_settings, init_settings, file_secret_settings)

    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "json"
    config_version: str = "local"

    ship: ShipIdentity | None = None
    """Kosong berarti perangkat **belum di-provisioning**.

    Dulu ini wajib, dan akibatnya agent menolak start di perangkat baru. Itu
    menciptakan lingkaran: Edge Console berjalan di dalam proses agent, jadi
    tanpa agent tidak ada antarmuka untuk memasukkan identitasnya — teknisi
    terpaksa menyunting YAML lewat SSH.

    Sekarang agent tetap menyala dalam mode setup: Console saja, tanpa akuisisi
    dan tanpa sinkronisasi, sampai identitas diisi lewat halaman setup.
    """
    storage: StorageSettings = Field(default_factory=StorageSettings)
    collector: CollectorSettings = Field(default_factory=CollectorSettings)
    sync: SyncSettings = Field(default_factory=SyncSettings)
    console: ConsoleSettings = Field(default_factory=ConsoleSettings)

    config_path: Path | None = Field(default=None, exclude=True)
    """Berkas YAML asal config ini, bila ada.

    Diperlukan halaman setup untuk tahu ke mana identitas harus dituliskan.
    Ditandai `exclude` supaya tidak ikut terbawa saat config di-dump ke Console
    atau ke log — ia jalur berkas, bukan setelan.
    """

    @property
    def is_configured(self) -> bool:
        """Perangkat siap mengumpulkan dan menyetor data.

        Butuh identitas kapal **dan** kredensial device. Identitas tanpa
        kredensial berarti akuisisi berjalan tetapi tidak ada yang bisa
        disetorkan — outbox menumpuk tanpa ada yang menyadari.
        """
        return self.ship is not None and bool(
            self.sync.device_token or (self.sync.device_client_id and self.sync.device_secret)
        )

    @property
    def ship_label(self) -> str:
        """Nama kapal untuk ditampilkan, aman dipakai sebelum provisioning."""
        return self.ship.ship_name if self.ship else "(belum dikonfigurasi)"

    @property
    def ship_id_label(self) -> str:
        return str(self.ship.ship_id) if self.ship else "-"

    def require_ship(self) -> ShipIdentity:
        """Identitas kapal, untuk jalur yang hanya berjalan setelah provisioning.

        Melempar, bukan mengembalikan None: akuisisi dan sinkronisasi tidak punya
        perilaku yang masuk akal tanpa identitas — data tanpa pemilik lebih buruk
        daripada tidak ada data.
        """
        if self.ship is None:
            raise ConfigError(
                "perangkat belum di-provisioning; buka Edge Console lalu selesaikan setup",
                code="config.not_provisioned",
            )
        return self.ship

    @field_validator("log_level")
    @classmethod
    def _check_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level harus salah satu dari {sorted(allowed)}, dapat {v!r}")
        return upper

    def verify_production_ready(self) -> None:
        """Gagal cepat bila config produksi punya lubang.

        Dipanggil saat startup. Lebih baik agent menolak jalan daripada berjalan
        24/7 di kapal terpencil dengan token kosong dan diam-diam tidak pernah
        berhasil sinkron.
        """
        if self.environment != "production":
            return

        # Perangkat yang belum di-provisioning tidak diperiksa: ia memang belum
        # punya apa-apa, dan menolak start justru menghilangkan satu-satunya
        # antarmuka untuk mengisinya.
        #
        # Yang diperiksa `ship is None`, BUKAN `is_configured`. Begitu identitas
        # kapal terisi, perangkat menyatakan dirinya sudah di-provisioning, dan
        # sejak titik itu kredensial yang hilang adalah salah konfigurasi yang
        # harus dilaporkan — bukan alasan untuk melewati pemeriksaan.
        if self.ship is None:
            return

        problems: list[str] = []
        if not self.storage.influx_token:
            problems.append("storage.influx_token kosong")
        if not self.sync.central_url:
            problems.append("sync.central_url kosong")
        if self.log_format != "json":
            problems.append("log_format harus 'json' di produksi supaya log bisa diagregasi")
        if self.collector.adapter != "lp_a104":
            problems.append(
                f"collector.adapter adalah {self.collector.adapter!r} — data simulasi/mock "
                f"tidak boleh dipakai di produksi"
            )
        if self.collector.sensors_path is None:
            problems.append("collector.sensors_path kosong")
        if self.console.host == "0.0.0.0" and not self.console.allow_lan:
            problems.append(
                "console.host adalah 0.0.0.0 tetapi console.allow_lan belum disetel — "
                "Edge Console tidak punya autentikasi, jadi membukanya ke jaringan "
                "kapal harus keputusan sadar. Setel console.allow_lan: true bila memang "
                "diinginkan, atau ikat ke 127.0.0.1 dan akses lewat port-forward SSH"
            )
        if problems:
            raise ConfigError(
                "config produksi tidak lengkap: " + "; ".join(problems),
                code="config.production_incomplete",
                details={"problems": problems},
            )


def load_settings(config_path: Path | None = None) -> EdgeSettings:
    """Muat config dari berkas YAML dan environment variable.

    Args:
        config_path: berkas YAML opsional. Nilainya jadi lapisan dasar;
            environment variable tetap menimpanya.

    Raises:
        ConfigError: berkas tidak ada, YAML rusak, atau config tidak valid.
    """
    base: dict[str, Any] = {}

    if config_path is not None:
        if not config_path.is_file():
            raise ConfigError(
                f"berkas config tidak ditemukan: {config_path}",
                code="config.file_missing",
                details={"path": str(config_path)},
            )
        try:
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(
                f"YAML rusak di {config_path}: {exc}",
                code="config.yaml_invalid",
                details={"path": str(config_path)},
            ) from exc
        if loaded is not None:
            if not isinstance(loaded, dict):
                raise ConfigError(
                    f"config harus berupa mapping, dapat {type(loaded).__name__}",
                    code="config.yaml_invalid",
                    details={"path": str(config_path)},
                )
            base = loaded

    try:
        return EdgeSettings(**base, config_path=config_path)
    except Exception as exc:  # pydantic ValidationError
        raise ConfigError(
            f"config tidak valid: {exc}",
            code="config.invalid",
            details={"path": str(config_path) if config_path else None},
        ) from exc
