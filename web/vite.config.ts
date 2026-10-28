import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  // shown in My account, so a phone and a browser can be compared at a glance
  define: { __BUILD_ID__: JSON.stringify(new Date().toISOString().slice(0, 16).replace("T", " ") + " UTC") },
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5173,
    // dev only: proxy /api to the local FastAPI so there's no CORS dance
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
