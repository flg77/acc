// Playwright over the real acc-webgui — the FastAPI app serving a real
// `vite build`, against the fixture backend in e2e/mock_webgui.py (a fake bus
// + fake Kubernetes API, ACC_WEBGUI_STATIC_DIR pointed at this build). Two
// projects, one per environment the SPA has to tell apart: `cluster` and
// `edge`. `npm run e2e` builds first (see package.json) — the webServer
// itself only starts the fixture, it does not rebuild the SPA.

import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

// "type": "module" in package.json means this file has no __dirname.
const WEBGUI_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(WEBGUI_DIR, "..");
const DIST_DIR = path.join(WEBGUI_DIR, "dist");
const PY = process.env.ACC_E2E_PYTHON || "python";

const backend = (mode: "cluster" | "edge", port: number) => ({
  command: `${PY} webgui/e2e/mock_webgui.py`,
  cwd: REPO_ROOT,
  env: { MODE: mode, PORT: String(port), ACC_WEBGUI_STATIC_DIR: DIST_DIR },
  url: `http://127.0.0.1:${port}/health`,
  reuseExistingServer: !process.env.CI,
  timeout: 30_000,
});

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  // Each project's whole run shares ONE long-lived backend process (the
  // catalog-mutating tests in packages.spec.ts write real state to it).
  // fullyParallel would let tests within that file race each other; the
  // default (parallel across files, sequential within one) keeps them safe
  // without hand-rolled isolation.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "cluster",
      use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:8180" },
    },
    {
      name: "edge",
      use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:8181" },
    },
  ],
  webServer: [backend("cluster", 8180), backend("edge", 8181)],
});
