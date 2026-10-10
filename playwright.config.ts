import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const databasePath = join(
  mkdtempSync(join(tmpdir(), "training-ai-browser-")),
  "chat.sqlite3",
);

export default defineConfig({
  testDir: "./tests/frontend",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:7933", browserName: "chromium" },
  webServer: {
    command:
      ".venv/bin/python -m uvicorn browser_server:app --host 127.0.0.1 --port 7933",
    env: {
      PYTHONPATH: "src:tests",
      DATABASE_PATH: databasePath,
      PYDANTIC_AI_NO_BANNER: "1",
    },
    url: "http://127.0.0.1:7933",
    reuseExistingServer: false,
  },
});
