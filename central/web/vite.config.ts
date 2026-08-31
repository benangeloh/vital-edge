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
    // Budget dari docs: halaman app < 300 KB JS ter-gzip.
    chunkSizeWarningLimit: 300,
  },
});
