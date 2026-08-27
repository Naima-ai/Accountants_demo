import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api to the FastAPI backend (uvicorn src.main:app,
// default port 8000) so the frontend can always call same-origin
// relative paths -- no CORS-sensitive base URL juggling between dev
// and the production build (which FastAPI serves directly, same
// origin as the API, from frontend/dist/).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
