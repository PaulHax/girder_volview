import { defineConfig, devices } from '@playwright/test';
import { CONFIG } from './helpers/config';

export default defineConfig({
  testDir: './tests',
  // Self-provisioning: globalSetup health-checks the stack + verifies the deploy
  // receipt, then creates a fresh folder + synthetic images; globalTeardown
  // removes them. See helpers/provision.ts + README.md.
  globalSetup: require.resolve('./global.setup'),
  globalTeardown: require.resolve('./global.teardown'),
  // A full launch + render + save + reload round-trip is slow.
  timeout: 180_000,
  expect: { timeout: 45_000 },
  // The lifecycle is stateful (a save must precede its F5-restore), so run the
  // gestures serially in one worker.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  outputDir: 'test-results',
  use: {
    baseURL: CONFIG.baseURL,
    // Fully automated + headless. `npx playwright test --headed` still works for a
    // manual look, but it is not part of the workflow.
    headless: true,
    // A screenshot at every checkpoint is the whole point (human eyeball).
    screenshot: 'on',
    trace: 'on',
    video: 'retain-on-failure',
    ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 1000 },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
