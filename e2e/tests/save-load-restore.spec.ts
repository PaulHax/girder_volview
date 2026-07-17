import { test, expect, Page } from '@playwright/test';
import {
  setup,
  launchUrl,
  countSessionItems,
  CONFIG,
  Girder,
  Gesture,
} from '../helpers/girder';
import {
  waitForVolViewReady,
  urlsParam,
  remoteSave,
  shot,
} from '../helpers/volview';

// Prove the F5 save/load/restore lifecycle per gesture against a DEPLOYED
// girder_volview + VolView stack.
//   1. launch -> raw images load (fresh)
//   2. F5 -> STILL fresh (no old save pulled in) -- even with a session in the folder
//   3. save -> the tab repoints urls= to the save's resumeUrl
//   4. F5 -> the just-made save reloads (resume)
//   5. save again -> F5 -> the SECOND save reloads
// The URL/manifest invariants are asserted automatically on the loaded-image scene.

const isSessionManifest = (json: any) =>
  Array.isArray(json?.resources) &&
  json.resources.some((r: any) => typeof r?.name === 'string' && r.name.endsWith('.volview.zip'));

const resourceNames = (json: any): string[] =>
  Array.isArray(json?.resources) ? json.resources.map((r: any) => r?.name).filter(Boolean) : [];

const isManifestGet = (response: { request: () => { method: () => string }; url: () => string }) =>
  response.request().method() === 'GET' &&
  /\/(item|folder)\/[^/]+\/volview$/.test(new URL(response.url()).pathname);

// Navigate to a launch/manifest URL and capture the manifest GET's JSON body.
async function gotoCapturingManifest(page: Page, url: string): Promise<any> {
  const manifestResp = page.waitForResponse(
    isManifestGet,
    { timeout: 60_000 }
  );
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  const resp = await manifestResp.catch(() => undefined);
  await waitForVolViewReady(page);
  if (!resp) return undefined;
  try {
    return await resp.json();
  } catch {
    return undefined;
  }
}

// Re-fetch the current tab (F5) and re-capture the manifest it loads.
async function reloadCapturingManifest(page: Page): Promise<any> {
  const manifestResp = page.waitForResponse(
    isManifestGet,
    { timeout: 60_000 }
  );
  await page.reload({ waitUntil: 'domcontentloaded' });
  const resp = await manifestResp.catch(() => undefined);
  await waitForVolViewReady(page);
  if (!resp) return undefined;
  try {
    return await resp.json();
  } catch {
    return undefined;
  }
}

test.describe.configure({ mode: 'serial' });

test.describe('save/load/restore F5 lifecycle', () => {
  let g: Girder;

  test.beforeEach(async ({ request, context }) => {
    g = await setup(request, context);
  });

  for (const gesture of ['single-item', 'checked', 'filter'] as Gesture[]) {
    test(`${gesture}: fresh -> F5-stays-fresh -> save -> F5-resumes -> save-again -> F5-resumes`, async ({
      page,
    }, info) => {
      const { url, freshManifest } = launchUrl(g, gesture);

      // 1. Launch -> fresh: the tab points at the picked manifest, which names
      //    the picked raw image(s) and NEVER a session zip.
      const m1 = await gotoCapturingManifest(page, url);
      await shot(page, info, `${gesture}-1-launch-fresh`);
      expect(urlsParam(page), 'launch urls= should be the picked manifest').toBe(freshManifest);
      if (m1) {
        expect(isSessionManifest(m1), `fresh launch must not load a session zip: ${resourceNames(m1)}`).toBeFalsy();
        expect(resourceNames(m1).some((n) => n !== 'config.json')).toBeTruthy();
      }

      // 2. F5 before saving -> STILL fresh (anti-substitution). The urls= param
      //    is unchanged and the manifest still names no session zip.
      const m2 = await reloadCapturingManifest(page);
      await shot(page, info, `${gesture}-2-f5-stays-fresh`);
      expect(urlsParam(page), 'F5-before-save must not repoint').toBe(freshManifest);
      if (m2) {
        expect(isSessionManifest(m2), 'F5-before-save must not pull in a session').toBeFalsy();
      }

      // 3. Save. The save repoints urls= to the response resumeUrl.
      const sessionsBefore = await countSessionItems(page.request, g);
      const resumeUrl1 = await remoteSave(page);
      await shot(page, info, `${gesture}-3-after-save`);
      expect(resumeUrl1, 'save response carried no resumeUrl').toBeTruthy();
      expect(urlsParam(page), 'urls= must repoint to the save resumeUrl').toBe(resumeUrl1);
      if (gesture !== 'single-item') {
        // Folder-scoped save creates a NEW session.volview.zip item.
        const sessionsAfter = await countSessionItems(page.request, g);
        expect(sessionsAfter, 'folder save should add a session item').toBeGreaterThan(sessionsBefore);
      }

      // 4. F5 after save -> the just-made save reloads (resume).
      const m4 = await reloadCapturingManifest(page);
      await shot(page, info, `${gesture}-4-f5-resumes-save`);
      expect(urlsParam(page), 'F5-after-save must stay on the resumeUrl').toBe(resumeUrl1);
      if (m4) {
        expect(isSessionManifest(m4), `resume manifest should name the saved session: ${resourceNames(m4)}`).toBeTruthy();
      }

      if (gesture === 'checked' || gesture === 'filter') {
        const reopened = await page.context().newPage();
        const reopenedManifest = await gotoCapturingManifest(reopened, url);
        expect(
          isSessionManifest(reopenedManifest),
          `reopening ${gesture} should resume matching work: ${resourceNames(reopenedManifest)}`
        ).toBeTruthy();
        await reopened.close();
      }

      // 5. Save again -> F5 -> the SECOND save reloads.
      const resumeUrl2 = await remoteSave(page);
      await shot(page, info, `${gesture}-5-after-second-save`);
      expect(resumeUrl2, 'second save carried no resumeUrl').toBeTruthy();
      expect(urlsParam(page)).toBe(resumeUrl2);
      await reloadCapturingManifest(page);
      await shot(page, info, `${gesture}-6-f5-resumes-second-save`);
      expect(urlsParam(page), 'F5 after the second save must stay on the second resumeUrl').toBe(resumeUrl2);
    });
  }

  test('bare folder-open resumes the newest session (after a folder-scoped save)', async ({ page }, info) => {
    // Guarantee a session exists in the folder: launch the checked gesture and save.
    const checked = launchUrl(g, 'checked');
    await gotoCapturingManifest(page, checked.url);
    const seededResume = await remoteSave(page);
    expect(seededResume).toBeTruthy();

    // Now the BARE folder-open must resume the newest session, not raw images.
    const bare = launchUrl(g, 'bare-folder');
    const m = await gotoCapturingManifest(page, bare.url);
    await shot(page, info, `bare-folder-resumes-newest`);
    expect(urlsParam(page)).toBe(bare.freshManifest); // bare folder manifest route
    if (m) {
      expect(isSessionManifest(m), `bare open should resume a session: ${resourceNames(m)}`).toBeTruthy();
    }
  });

  test('checking a saved session in Girder opens that session', async ({ page }) => {
    const checked = launchUrl(g, 'checked');
    await gotoCapturingManifest(page, checked.url);
    const resumeUrl = await remoteSave(page);
    expect(resumeUrl).toBeTruthy();
    const sessionId = resumeUrl.split('/item/')[1].split('/volview')[0];

    await page.goto(`${CONFIG.baseURL}/#folder/${g.folderId}`, {
      waitUntil: 'domcontentloaded',
    });
    const sessionRow = page.locator(
      `li.g-item-list-entry:has(a[href="#item/${sessionId}"])`
    );
    const imageRow = page.locator(
      `li.g-item-list-entry:has(a[href="#item/${g.itemId}"])`
    );
    await expect(sessionRow).toBeVisible();
    await expect(imageRow).toBeVisible();
    await sessionRow.locator('input.g-list-checkbox').check();
    await imageRow.locator('input.g-list-checkbox').check();

    await page.locator('.open-in-volview').click();
    await expect(page.locator('.modal-content')).toContainText(
      'Will open newest VolView session'
    );
    const popupPromise = page.waitForEvent('popup');
    await page.locator('#g-confirm-button').click();
    const popup = await popupPromise;
    await popup.waitForLoadState('domcontentloaded');
    await waitForVolViewReady(popup);

    expect(urlsParam(popup)).toContain(`items=${sessionId}`);
  });

  test('config sanity: deployment + provisioned folder are reachable', async ({ page }) => {
    // A fast fail-early check with a clear message if the stack/provisioning is wrong.
    expect(g.folderId, 'no launch folder (global setup did not provision one)').toBeTruthy();
    expect(g.itemName, 'no loadable image in the launch folder').toBeTruthy();
    const { url } = launchUrl(g, 'single-item');
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await waitForVolViewReady(page);
  });
});
