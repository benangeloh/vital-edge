import { Suspense, lazy, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Sidebar } from "@/components/shell/Sidebar";
import { TopBar } from "@/components/shell/TopBar";
import { FleetOverview } from "@/routes/FleetOverview";
import { Login } from "@/routes/Login";

// Detail kapal dimuat terpisah. Di situlah uPlot dipakai, dan halaman yang
// paling sering dibuka operator — ikhtisar armada — tidak membutuhkannya.
// Memuatnya di bundel utama berarti setiap kali dashboard dibuka, kode chart
// ikut diunduh meski belum tentu dipakai.
const ShipDetail = lazy(() =>
  import("@/routes/ShipDetail").then((m) => ({ default: m.ShipDetail })),
);

import {
  AlertsPage,
  DevicesPage,
  PlaceholderPage,
  ShipsPage,
  SyncCenterPage,
  SystemHealthPage,
} from "@/routes/Pages";
import { auth } from "@/lib/api";
import { useAlerts, useFleet } from "@/lib/queries";
import { needsAttention, summarise } from "@/lib/status";

const client = new QueryClient({
  defaultOptions: {
    queries: {
      // Kegagalan yang tidak retryable tidak diulang — sama seperti aturan di
      // Sync Engine. Mengulang permintaan yang pasti ditolak hanya menambah
      // beban dan memperlambat pesan error sampai ke operator.
      retry: (failureCount, error) => {
        const retryable = (error as { error?: { retryable?: boolean } })?.error?.retryable;
        return retryable !== false && failureCount < 2;
      },
      staleTime: 5_000,
    },
  },
});

function Shell() {
  const [search, setSearch] = useState("");
  const { data: ships } = useFleet();
  const { data: alerts } = useAlerts();

  const summary = ships ? summarise(ships) : undefined;
  const attentionCount = ships ? needsAttention(ships).length : 0;
  const openAlerts = (alerts ?? []).filter((a) => !a.acknowledged_at).length;

  return (
    <div className="app">
      <a href="#main" className="skip-link">
        Lompat ke konten utama
      </a>
      <Sidebar />
      <div className="app__body">
        <TopBar
          summary={summary}
          attentionCount={attentionCount}
          search={search}
          onSearch={setSearch}
          alertCount={openAlerts}
        />
        <main id="main" className="content" tabIndex={-1}>
          <Routes>
            <Route path="/" element={<FleetOverview search={search} />} />
            <Route path="/ships" element={<ShipsPage search={search} />} />
            <Route
              path="/ships/:shipId"
              element={
                <Suspense fallback={<div className="loading">Memuat kapal…</div>}>
                  <ShipDetail />
                </Suspense>
              }
            />
            <Route path="/alerts" element={<AlertsPage />} />
            <Route
              path="/telemetry"
              element={
                <PlaceholderPage
                  title="Telemetry"
                  note="Penjelajah telemetry lintas kapal menyusul. Chart per kapal sudah tersedia di halaman detail kapal."
                />
              }
            />
            <Route path="/sync" element={<SyncCenterPage />} />
            <Route path="/devices" element={<DevicesPage />} />
            <Route
              path="/configuration"
              element={
                <PlaceholderPage
                  title="Konfigurasi"
                  note="Penyuntingan konfigurasi berversi menyusul. Riwayat versi per kapal ada di halaman detail kapal."
                />
              }
            />
            <Route
              path="/reports"
              element={
                <PlaceholderPage
                  title="Laporan"
                  note="Laporan menyusul setelah agregasi dan downsampling tersedia."
                />
              }
            />
            <Route path="/system" element={<SystemHealthPage />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const [token, setToken] = useState(auth.token);

  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        {token ? <Shell /> : <Login onAuthenticated={() => setToken(auth.token)} />}
      </BrowserRouter>
    </QueryClientProvider>
  );
}
