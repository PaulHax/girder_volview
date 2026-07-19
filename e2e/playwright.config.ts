import { defineConfig, devices } from '@playwright/test';
import { CONFIG } from './helpers/config';

export default defineConfig({
  testDir: './tests',
  // Compat specs run only under compat.playwright.config.ts (they need the
  // two-deploy orchestration and its persisted state).
  testIgnore: '**/compat/**',
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
    headless: true,
    // Checkpoint screenshots are a deliverable of this suite, not just failure
    // debris.
    screenshot: 'on',
    trace: 'on',
    video: 'retain-on-failure',
    ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 1000 },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
