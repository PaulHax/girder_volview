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
  showJobResults,
  applyResult,
  expectAppliedToast,
  selectTask,
  waitForInputBound,
  submitTaskFromForm,
  waitForJobComplete,
  shot,
} from '../helpers/volview';

// The jobs/processing plane in the browser (complements the Python REST test).
// Setup submits an Otsu job over REST (folder+user scoped to the same admin the
// tab runs as), polls it to success, then each test launches VolView on that
// folder — the launch carries config= (which is what makes the Jobs tab appear)
// and loads the image as a base (needed for the layer/segment-group applies) —
// opens Jobs -> "Show results", clicks an apply action, and asserts the apply
// signal + the "Applied …" toast.

test.describe.configure({ mode: 'serial' });

test.describe('jobs run + apply path', () => {
  // Submit the Otsu job once (REST) for the whole suite; the launched tabs just
  // apply its result.
  test.beforeAll(async () => {
    const req = await playwrightRequest.newContext({ ignoreHTTPSErrors: true });
    try {
      const token = await login(req);
      const { folderId } = resolveContext();
      const { itemId } = await resolveImageItem(req, token, folderId);
      const { jobId, state } = await submitOtsu(req, token, folderId, itemId);
      expect(state, `Otsu job ${jobId} did not succeed (state=${state})`).toBe('success');
      // eslint-disable-next-line no-console
      console.log(`[e2e] Otsu job ${jobId} succeeded — folder ${folderId} ready for apply tests`);
    } finally {
      await req.dispose();
    }
  });

  let g: Girder;
  test.beforeEach(async ({ request, context }) => {
    g = await setup(request, context);
  });

  // Launch VolView on the folder with the image as a base + config= (Jobs tab),
  // then reveal the job results.
  async function launchToJobResults(page: import('@playwright/test').Page) {
    const { url } = launchUrl(g, 'checked'); // loads g.itemId as base + config=
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await waitForVolViewReady(page);
    await showJobResults(page);
  }

  test('Jobs tab lists the succeeded job and reveals a result', async ({ page }, info) => {
    await launchToJobResults(page);
    await shot(page, info, 'jobs-tab-results');
    await expect(page.locator('.jobs-module .result-row').first()).toBeVisible();
  });

  test('Add as segment group → new-job-result chip + Applied toast', async ({ page }, info) => {
    await launchToJobResults(page);
    await applyResult(page, 'Add as segment group');
    await expectAppliedToast(page);
    // The result becomes a segment group tagged "new job result" in Annotations.
    await openModuleTab(page, 'Annotations');
    await expect(
      page.locator('.segment-group-list').getByText('new job result').first(),
      'no "new job result" chip in the segment-group list'
    ).toBeVisible();
    await shot(page, info, 'apply-segment-group');
  });

  test('Add as layer → new layer slider + Applied toast', async ({ page }, info) => {
    await launchToJobResults(page);
    await applyResult(page, 'Add as layer');
    await expectAppliedToast(page);
    // A layer with an opacity slider now exists in Rendering.
    await openModuleTab(page, 'Rendering');
    await expect(
      page.locator('[data-testid="layer-opacity-slider"]').first(),
      'no layer-opacity-slider after Add as layer'
    ).toBeVisible();
    await shot(page, info, 'apply-layer');
  });

  test('Open → new dataset + Applied toast', async ({ page }, info) => {
    const { url } = launchUrl(g, 'checked');
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await waitForVolViewReady(page);

    // Baseline image count in the Data tab (the one launched image), then apply.
    // Non-DICOM images render in ImageDataBrowser, one `.dataset-menu` per image
    // (the `dataset-menu-button` testid is the DICOM PatientStudyVolumeBrowser).
    await openModuleTab(page, 'Data');
    const datasets = page.locator('.dataset-menu');
    const before = await datasets.count();

    await showJobResults(page);
    await applyResult(page, 'Open');
    await expectAppliedToast(page);

    // "Open" loads the result as a new base dataset.
    await openModuleTab(page, 'Data');
    await expect
      .poll(() => datasets.count(), { message: 'Open did not add a dataset', timeout: 20_000 })
      .toBeGreaterThan(before);
    await shot(page, info, 'apply-open');
  });
});

// ---------------------------------------------------------------------------
// The CI SUBMISSION GATE (token-only). Drives the full visible UI path — task
// picker -> task form -> provenance binding -> Submit -> poll -> authenticated
// result stream -> LIVE auto-apply — with token-only auth (no girderToken
// cookie). This is the gate that fails if selection, submission, polling,
// authenticated byte download, or live auto-apply regresses. The REST-seeded
// manual-apply cases above are kept for history/manual-apply coverage only.
// ---------------------------------------------------------------------------
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
      // auto-apply attached the result with NO manual "Show results"/apply click:
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
