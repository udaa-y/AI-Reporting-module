import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// In development, API calls go through Vite's proxy to FastAPI,
// so the browser sees a single origin and no CORS setup is needed.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backend = env.VITE_BACKEND_URL || "http://localhost:8000";
  return {
    plugins: [react()],
    build: {
      rollupOptions: {
        // ECharts in its own file: cached separately from app code between deploys.
        output: { manualChunks: { echarts: ["echarts/core", "zrender"] } },
      },
    },
    server: {
      port: 5173,
      proxy: {
        "/dashboard": backend,
        "/query": backend,
        "/health": backend,
        "/auth": backend,
        "/assistant": backend,
        "/vanna": backend,
      },
    },
  };
});
