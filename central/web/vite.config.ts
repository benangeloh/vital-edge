import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      // Dashboard bicara ke Central API lewat path relatif, sehingga produksi
      // tidak butuh base URL terpisah dan tidak butuh CORS.
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    // Budget di docs/architecture ditetapkan pada ukuran TER-GZIP (< 300 KB),
    // sementara ambang Vite mengukur ukuran mentah. Angka di bawah kira-kira
    // setara dengan budget itu, supaya peringatannya bermakna alih-alih selalu
    // menyala. Ukuran gzip yang sesungguhnya dicetak di keluaran build.
    chunkSizeWarningLimit: 900,
  },
});
