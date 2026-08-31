# shared/ — dipakai oleh KEDUA sistem

Kode di sini di-deploy **ke kapal maupun ke pusat**. Karena itu ia tidak boleh
mengandung logic bisnis dan tidak boleh mengimpor apa pun dari `edge/` atau `central/`.

| Paket | Distribusi | Isi |
|---|---|---|
| `contracts/` | `fleetview-contracts` + `@fleetview/contracts` | Format wire: Reading, BatchEnvelope, Ack, SyncState, Heartbeat, ExportManifest |
| `common/` | `fleetview-common` | Logging, taksonomi error, correlation ID, utilitas waktu |

Aturan yang menjaga ini tetap sehat:

1. `contracts` tidak bergantung pada apa pun selain Pydantic.
2. `common` tidak bergantung pada `contracts`.
3. Mengubah `contracts` berarti mengubah format yang beredar di 70 kapal —
   ikuti aturan versi di [contracts/CHANGELOG.md](contracts/CHANGELOG.md).

Model Pydantic adalah sumber kebenarannya; `contracts/schemas/*.json` dan tipe
TypeScript dihasilkan darinya. Regenerate dengan `make schemas`.
