import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "@fontsource-variable/bricolage-grotesque";
import App from "@/App";
import "@/styles/tokens.css";
import "@/styles/app.css";
import "@/styles/health.css";
import "@/styles/overview.css";
import "@/styles/logger.css";

const root = document.getElementById("root");
if (!root) throw new Error("elemen #root tidak ditemukan");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
