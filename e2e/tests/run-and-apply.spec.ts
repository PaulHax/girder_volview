import { test, expect, Page } from '@playwright/test';
import { setupFixture, requireHarnessState, Girder } from '../helpers/girder';
import { requireFixture } from '../helpers/compat-state';
import { gotoFolder, checkRowByItemId, openInVolView } from '../helpers/girder-ui';
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

async function launchChecked(driver: Page, g: Girder): Promise<Page> {
  await gotoFolder(driver, g.folderId);
  await checkRowByItemId(driver, g.itemId);
  const launch = await openInVolView(driver);
  await waitForVolViewReady(launch.popup);
  return launch.popup;
}

test.describe('jobs come-back path (Load results)', () => {
  // Submit the Otsu job once (REST) for the whole suite; the launched tab just
  // loads its result.
  test.beforeAll(async ({ request }) => {
    const state0 = requireHarnessState();
    const fixture = requireFixture(state0, 'jobs-comeback');
    const { jobId, state } = await submitOtsu(
      request,
      state0.token,
      fixture.folderId,
      fixture.itemIds[0]
    );
    expect(state, `Otsu job ${jobId} did not succeed (state=${state})`).toBe('success');
    // eslint-disable-next-line no-console
    console.log(
      `[e2e] Otsu job ${jobId} succeeded — folder ${fixture.folderId} ready for come-back test`
    );
  });

  test('Load results applies the labelmap as a segment group on the original image', async ({
    page,
    context,
  }, info) => {
    const g = await setupFixture(context, 'jobs-comeback');
    const view = await launchChecked(page, g);

    // The come-back job must NOT auto-apply: before the explicit load there is
    // no Otsu segment group and no loaded-result count.
    await openModuleTab(view, 'Annotations');
    await expect(
      view.locator('.segment-group-list').getByText(/Otsu/),
      'a history job auto-applied without "Load"'
    ).toHaveCount(0);

    await loadJobResults(view);
    await shot(view, info, 'jobs-tab-results');
    // The button is consumed: loading = applying, exactly once.
    await expect(
      view.locator('.jobs-module').getByRole('button', { name: 'Load', exact: true })
    ).toHaveCount(0);

    // Intent-honoring apply: the labelmap became an "<image>.<Task>" segment
    // group on the reconstructed parent image (no manual verb choice).
    await openModuleTab(view, 'Annotations');
    await expect(
      view.locator('.segment-group-list').getByText(/Otsu/).first(),
      'no Otsu segment group in the segment-group list'
    ).toBeVisible({ timeout: 30_000 });
    await shot(view, info, 'come-back-apply');
  });
});

// Drives the full visible UI path — task picker, form, provenance binding,
// Submit, poll, result stream, live auto-apply — under the product's cookie
// auth (the girder launcher's popup shares the session cookie; this girder
// does not honor Authorization: Bearer). The come-back suite above covers the
// explicit "Load" path only.
test.describe('live submission + auto-apply (the submission gate)', () => {
  test('submits from the UI and live-auto-applies the result', async ({
    page,
    context,
  }, info) => {
    const g = await setupFixture(context, 'jobs-live');
    const view = await launchChecked(page, g);

    // Result-byte reads go through proxiable file URLs; count them to prove the
    // result stream actually flowed.
    const fileReads: string[] = [];
    view.on('request', (r) => {
      if (/\/file\/[^/]+\/proxiable\//.test(r.url())) fileReads.push(r.url());
    });

    // Drive the VISIBLE submission flow: task picker -> binding -> Submit.
    await selectTask(view, 'Otsu');
    await waitForInputBound(view);
    await submitTaskFromForm(view);

    // Poll to live completion (the store's own toast), then confirm LIVE
    // auto-apply attached the result with NO manual "Load" click:
    // the Otsu labelmap becomes an "<image>.<Task>" segment group.
    await waitForJobComplete(view);
    await openModuleTab(view, 'Annotations');
    await expect(
      view.locator('.segment-group-list').getByText(/Otsu/).first(),
      'live auto-apply did not attach a segment group'
    ).toBeVisible({ timeout: 30_000 });
    await shot(view, info, 'live-auto-apply');

    expect(fileReads.length, 'no proxiable result file read observed').toBeGreaterThan(0);
  });
});
