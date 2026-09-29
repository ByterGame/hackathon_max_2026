import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    proxy: {
      "/auth": "http://127.0.0.1:8000",
      "/access": "http://127.0.0.1:8000",
      "/issues": "http://127.0.0.1:8000",
      "/notifications": "http://127.0.0.1:8000",
      "/drafts": "http://127.0.0.1:8000",
      "/files": "http://127.0.0.1:8000",
      "/images": "http://127.0.0.1:8000",
      "/media": "http://127.0.0.1:8000",
    },
  },
});
