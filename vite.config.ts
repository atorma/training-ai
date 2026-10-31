import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// `make dev-web` serves the UI with hot reload and forwards API calls to Uvicorn.
const backend = `http://127.0.0.1:${process.env.WEB_PORT ?? "7932"}`;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src/frontend", import.meta.url)) },
  },
  build: { outDir: "src/training_ai/web_assets/static", emptyOutDir: true },
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": backend, "/summary": backend },
  },
});
