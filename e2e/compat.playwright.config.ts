import { defineConfig, devices } from '@playwright/test';
import { CONFIG } from './helpers/config';

// Backwards-compat suite: the capture project runs against a MAIN deploy and
// saves sessions; the verify project runs against THIS worktree's deploy and
// asserts those sessions still open. The two projects run in SEPARATE
// playwright invocations (a redeploy happens in between) — e2e/scripts/compat.sh
// orchestrates that; do not run this config bare.
export default defineConfig({
  testDir: './tests/compat',
  globalSetup: require.resolve('./compat.setup'),
  globalTeardown: require.resolve('./compat.teardown'),
  timeout: 240_000,
  expect: { timeout: 45_000 },
  // Capture appends to the shared state file; verify replays it in order.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  outputDir: 'test-results',
  use: {
    baseURL: CONFIG.baseURL,
    headless: true,
    screenshot: 'on',
    trace: 'on',
    video: 'retain-on-failure',
    ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 1000 },
  },
  projects: [
    {
      name: 'capture',
      testMatch: /.*capture\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'verify',
      testMatch: /.*verify\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
