import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/provider_bench/web/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 800,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
