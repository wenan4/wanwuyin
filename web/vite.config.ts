import { defineConfig } from "vite";

// Entry points: sound viewer, listening page, seal display, and maker workspace.
export default defineConfig({
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    rolldownOptions: { input: { main: "index.html", listen: "listen.html", seal: "seal.html", maker: "maker.html" } },
    chunkSizeWarningLimit: 900,
  },
});
