import { defineConfig } from "vite";

// Three pages: the 3D viewer, the screen-reader-first listening page, and the seal form concept.
export default defineConfig({
  build: {
    rolldownOptions: { input: { main: "index.html", listen: "listen.html", seal: "seal.html" } },
    chunkSizeWarningLimit: 900,
  },
});
