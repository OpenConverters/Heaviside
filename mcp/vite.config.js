import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// MCP App resources render in a deny-by-default CSP iframe, so the widget must be
// ONE self-contained file: no external script/style/font requests.
export default defineConfig({
  plugins: [viteSingleFile()],
  build: {
    outDir: "dist",
    emptyOutDir: false,
    rollupOptions: { input: process.env.INPUT || "results.html" },
  },
});
