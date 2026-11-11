import { defineConfig } from "vite";

// Three pages: the 3D viewer, the screen-reader-first listening page, and the seal form concept.
export default defineConfig({
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    rolldownOptions: { input: { main: "index.html", listen: "listen.html", seal: "seal.html" } },
    chunkSizeWarningLimit: 900,
  },
});
