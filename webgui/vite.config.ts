import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The production build emits static assets that PR-6's Containerfile
// copies into the FastAPI image at acc/webgui/static.  In dev, the
// Vite server proxies /api and /ws to the local FastAPI backend.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/health": "http://127.0.0.1:8080",
      "/ws": { target: "ws://127.0.0.1:8080", ws: true },
    },
  },
});
