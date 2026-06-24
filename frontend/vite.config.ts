import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` serves the SPA on :5173 and proxies /api → the FastAPI backend (uvicorn :8181;
// over the SSH tunnel point it at the tunneled port). Build: emits the committed bundle into the
// Python package at src/poc_foundry/web/dist/ — exactly where web/server.py serves it from. Rebuild +
// recommit dist/ after any frontend change (the server has no npm; rule #3).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8181", changeOrigin: true },
    },
  },
  build: {
    outDir: "../src/poc_foundry/web/dist",
    emptyOutDir: true,
    assetsDir: "assets",
  },
});
