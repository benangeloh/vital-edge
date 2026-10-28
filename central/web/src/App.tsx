import { Suspense, lazy, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { TopBar } from "@/components/shell/TopBar";
import { FleetOverview } from "@/routes/FleetOverview";
import { Login } from "@/routes/Login";

// Detail kapal dimuat terpisah. Di situlah uPlot dipakai, dan halaman yang
// paling sering dibuka operator — ringkasan armada — tidak membutuhkannya.
// Memuatnya di bundel utama berarti setiap kali dashboard dibuka, kode chart
// ikut diunduh meski belum tentu dipakai.
const ShipDetail = lazy(() =>
  import("@/routes/ShipDetail").then((m) => ({ default: m.ShipDetail })),
);

import { ShipsPage } from "@/routes/Ships";
import { auth } from "@/lib/api";
import { useFleet } from "@/lib/queries";
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

  const summary = ships ? summarise(ships) : undefined;
  const attentionCount = ships ? needsAttention(ships).length : 0;

  return (
    <div className="app">
      <a href="#main" className="skip-link">
        Lompat ke konten utama
      </a>
      <TopBar
        summary={summary}
        attentionCount={attentionCount}
        search={search}
        onSearch={setSearch}
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
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
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
