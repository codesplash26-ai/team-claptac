import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build lands in the Python package so the API serves it at /manager with no Node needed at run time.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../atlas/dashboard/static/manager",
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    proxy: {
      "/intelligence": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      "/am": "http://127.0.0.1:8000",
      "/dev": "http://127.0.0.1:8000",
      "/play": "http://127.0.0.1:8000",
      "/approvals": "http://127.0.0.1:8000",
      "/ledger": "http://127.0.0.1:8000",
      "/missions": "http://127.0.0.1:8000",
      "/alerts": "http://127.0.0.1:8000",
      "/sensors": "http://127.0.0.1:8000",
      "/incidents": "http://127.0.0.1:8000",
    },
  },
});
