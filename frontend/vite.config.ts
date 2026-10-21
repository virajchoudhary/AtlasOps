import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { readFile } from "node:fs/promises";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "/",
  plugins: [
    react(),
    tailwindcss(),
    {
      name: "media-license-notices",
      async generateBundle() {
        const notice = await readFile(new URL("./THIRD_PARTY_NOTICES.md", import.meta.url), "utf8");
        const normalizedNotice = notice.replace(/\r\n/g, "\n");
        for (const name of ["media-chrome", "ce-la-react"]) {
          const root = new URL(`./node_modules/${name}/`, import.meta.url);
          const metadata: unknown = JSON.parse(await readFile(new URL("package.json", root), "utf8"));
          const version = typeof metadata === "object" && metadata !== null && "version" in metadata
            ? metadata.version : undefined;
          if (typeof version !== "string") {
            throw new Error(`Missing package version for ${name}`);
          }
          const license = (await readFile(new URL("LICENSE", root), "utf8"))
            .replace(/\r\n/g, "\n").trimEnd();
          if (!normalizedNotice.includes(`${name}@${version}`)
            || !normalizedNotice.includes(license)) {
            throw new Error(`Add the complete LICENSE for ${name}@${version} to THIRD_PARTY_NOTICES.md`);
          }
        }
        this.emitFile({
          type: "asset",
          fileName: "assets/THIRD_PARTY_NOTICES.txt",
          source: notice,
        });
      },
    },
  ],
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: "http://127.0.0.1:7862",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    clearMocks: true,
    restoreMocks: true,
    // Node 25 enables its own global localStorage, which shadows jsdom's.
    execArgv: ["--no-experimental-webstorage"],
  },
});
