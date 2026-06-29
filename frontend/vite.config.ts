import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Minimal ambient type so we can read an env var here without pulling @types/node into the app.
declare const process: { env: Record<string, string | undefined> };

// Dev: proxy /api -> the FastAPI backend (avoids CORS, keeps one origin).
// Override the target with TM_BACKEND if your backend runs elsewhere.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.TM_BACKEND || "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
        ws: true,
      },
    },
  },
});
