import { test, expect, request as playwrightRequest } from '@playwright/test';
import {
  setup,
  login,
  resolveContext,
  resolveImageItem,
  launchUrl,
  Girder,
} from '../helpers/girder';
import { submitOtsu } from '../helpers/jobs';
import {
  waitForVolViewReady,
  openModuleTab,
  loadJobResults,
  selectTask,
  waitForInputBound,
  submitTaskFromForm,
  waitForJobComplete,
  shot,
} from '../helpers/volview';

// The jobs/processing plane in the browser. Setup submits an Otsu job over REST
// (folder+user scoped to the same admin the tab runs as) and polls it to
// success, so the launched tab meets a job that finished before it existed and
// must reach it through the come-back path. The launch carries config=, which
// is what makes the Jobs tab appear.

test.describe.configure({ mode: 'serial' });

test.describe('jobs come-back path (Load results)', () => {
  // Submit the Otsu job once (REST) for the whole suite; the launched tab just
  // loads its result.
  test.beforeAll(async () => {
    const req = await playwrightRequest.newContext({ ignoreHTTPSErrors: true });
    try {
      const token = await login(req);
      const { folderId } = resolveContext();
      const { itemId } = await resolveImageItem(req, token, folderId);
      const { jobId, state } = await submitOtsu(req, token, folderId, itemId);
      expect(state, `Otsu job ${jobId} did not succeed (state=${state})`).toBe('success');
      // eslint-disable-next-line no-console
      console.log(`[e2e] Otsu job ${jobId} succeeded — folder ${folderId} ready for come-back test`);
    } finally {
      await req.dispose();
    }
  });

  let g: Girder;
  test.beforeEach(async ({ request, context }) => {
    g = await setup(request, context);
  });

  test('Load results applies the labelmap as a segment group on the original image', async ({ page }, info) => {
    const { url } = launchUrl(g, 'checked'); // loads g.itemId as base + config=
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await waitForVolViewReady(page);

    // The come-back job must NOT auto-apply: before the explicit load there is
    // no segment group and no result row.
    await openModuleTab(page, 'Annotations');
    await expect(
      page.locator('.segment-group-list').getByText('new job result'),
      'a history job auto-applied without "Load results"'
    ).toHaveCount(0);

    await loadJobResults(page);
    await shot(page, info, 'jobs-tab-results');
    await expect(page.locator('.jobs-module .result-row').first()).toBeVisible();
    // The button is consumed: loading = applying, exactly once.
    await expect(
      page.locator('.jobs-module').getByRole('button', { name: 'Load results' })
    ).toHaveCount(0);

    // Intent-honoring apply: the labelmap became a "new job result" segment
    // group on the reconstructed parent image (no manual verb choice).
    await openModuleTab(page, 'Annotations');
    await expect(
      page.locator('.segment-group-list').getByText('new job result').first(),
      'no "new job result" chip in the segment-group list'
    ).toBeVisible({ timeout: 30_000 });
    await shot(page, info, 'come-back-apply');
  });
});

// Drives the full visible UI path — task picker, form, provenance binding,
// Submit, poll, authenticated result stream, live auto-apply — under token-only
// auth with no girderToken cookie. The come-back suite above covers the
// explicit "Load results" path only.
test.describe('token-only run + live auto-apply (the submission gate)', () => {
  test('launches token-only, submits from the UI, and live-auto-applies without a cookie', async ({
    browser,
  }, info) => {
    // An ISOLATED context so no girderToken cookie can leak from the cookie-based
    // block above — token-only means no cookie at all.
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    const req = await playwrightRequest.newContext({ ignoreHTTPSErrors: true });
    try {
      const token = await login(req);
      const { folderId } = resolveContext();
      const { itemId, itemName } = await resolveImageItem(req, token, folderId);
      const g: Girder = { token, folderId, itemId, itemName };

      const page = await context.newPage();

      // Track authenticated result-byte reads: the client fetches result files
      // via proxiable file URLs carrying the Authorization bearer ($fetch/pool).
      const authedFileReads: string[] = [];
      let sawUnauthedFileRead = false;
      page.on('request', (r) => {
        if (/\/file\/[^/]+\/proxiable\//.test(r.url())) {
          const auth = r.headers()['authorization'];
          if (auth && /^Bearer /i.test(auth)) authedFileReads.push(r.url());
          else sawUnauthedFileRead = true;
        }
      });

      // Launch token-only (no plantCookie): the ?token= leg sets the bearer.
      const { url } = launchUrl(g, 'checked', { token });
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      await waitForVolViewReady(page);

      // Drive the VISIBLE submission flow: task picker -> binding -> Submit.
      await selectTask(page, 'Otsu');
      await waitForInputBound(page);
      await submitTaskFromForm(page);

      // Poll to live completion (the store's own toast), then confirm LIVE
      // auto-apply attached the result with NO manual "Load results" click:
      // the Otsu labelmap becomes a "new job result" segment group.
      await waitForJobComplete(page);
      await openModuleTab(page, 'Annotations');
      await expect(
        page.locator('.segment-group-list').getByText('new job result').first(),
        'live auto-apply did not attach a segment group'
      ).toBeVisible({ timeout: 30_000 });
      await shot(page, info, 'token-only-auto-apply');

      // Token-only proof: an authenticated result-byte read happened, none went
      // out unauthenticated, and NO girderToken cookie ever existed.
      expect(
        authedFileReads.length,
        'no authenticated proxiable file read observed'
      ).toBeGreaterThan(0);
      expect(
        sawUnauthedFileRead,
        'a proxiable file read went out without a bearer'
      ).toBe(false);
      const cookies = await context.cookies();
      expect(
        cookies.find((c) => c.name === 'girderToken'),
        'a girderToken cookie existed in a token-only launch'
      ).toBeUndefined();
    } finally {
      await req.dispose();
      await context.close();
    }
  });
});
