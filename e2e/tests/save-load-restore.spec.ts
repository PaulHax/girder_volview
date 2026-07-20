import { test, expect, Page } from '@playwright/test';
import {
  gotoFolder,
  checkRowByItemId,
  uncheckAllRows,
  openInVolView,
  openFromItemPage,
} from '../helpers/girder-ui';
import {
  setup,
  launchUrl,
  countSessionItems,
  firstFileId,
  fetchManifest,
  resourceUrls,
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
import {
  isSessionManifest,
  resourceNames,
  gotoCapturingManifest,
  reloadCapturingManifest,
} from '../helpers/manifest';

// The F5 save/load/restore lifecycle per gesture, against a DEPLOYED
// girder_volview + VolView stack.

test.describe.configure({ mode: 'serial' });

// Launch a gesture the way a USER does: drive the girder UI so the plugin's own
// open.js builds the URL and window.open()s the tab. `driver` is the girder
// page; the returned `view` is the VolView tab (a popup for UI gestures).
//
// `filter` is the exception — it has no UI driver here: the gesture lives on
// large_image grouped rows, which need a .large_image_config.yaml plus
// meta._grouping that this suite's two synthetic NRRDs do not carry. It stays
// on the launchUrl() replica, which the URL-mirror test pins to open.js.
type Launched = { view: Page; freshManifest: string; manifest: any };

async function launchGesture(driver: Page, g: Girder, gesture: Gesture): Promise<Launched> {
  const predicted = launchUrl(g, gesture).freshManifest;
  if (gesture === 'filter') {
    const manifest = await gotoCapturingManifest(driver, launchUrl(g, gesture).url);
    return { view: driver, freshManifest: predicted, manifest };
  }
  if (gesture === 'single-item') {
    const launch = await openFromItemPage(driver, g.itemId);
    await waitForVolViewReady(launch.popup);
    return { view: launch.popup, freshManifest: predicted, manifest: await launch.manifest };
  }
  await gotoFolder(driver, g.folderId);
  // 'checked' checks the image row; 'bare-folder' clears every checkbox, which
  // is what turns the button into "Open Folder in VolView".
  if (gesture === 'checked') {
    await checkRowByItemId(driver, g.itemId);
  } else {
    await uncheckAllRows(driver);
  }
  const launch = await openInVolView(driver);
  await waitForVolViewReady(launch.popup);
  return { view: launch.popup, freshManifest: predicted, manifest: await launch.manifest };
}

test.describe('save/load/restore F5 lifecycle', () => {
  let g: Girder;

  test.beforeEach(async ({ request, context }) => {
    g = await setup(request, context);
  });

  for (const gesture of ['single-item', 'checked', 'filter'] as Gesture[]) {
    test(`${gesture}: fresh -> F5-stays-fresh -> save -> F5-resumes -> save-again -> F5-resumes`, async ({
      page,
    }, info) => {
      // 1. Launch through the real UI -> fresh: the picked manifest, never a
      //    session zip. The urls= assertion doubles as the drift alarm: it is
      //    what open.js actually emitted, checked against the replica.
      const launched = await launchGesture(page, g, gesture);
      const view = launched.view;
      const freshManifest = launched.freshManifest;
      const m1 = launched.manifest;
      await shot(view, info, `${gesture}-1-launch-fresh`);
      expect(urlsParam(view), 'launch urls= should be the picked manifest').toBe(freshManifest);
      if (m1) {
        expect(isSessionManifest(m1), `fresh launch must not load a session zip: ${resourceNames(m1)}`).toBeFalsy();
        expect(resourceNames(m1).some((n) => n !== 'config.json')).toBeTruthy();
      }

      // 2. F5 before saving -> STILL fresh: a session already in the folder must
      //    not be substituted for the picked images.
      const m2 = await reloadCapturingManifest(view);
      await shot(view, info, `${gesture}-2-f5-stays-fresh`);
      expect(urlsParam(view), 'F5-before-save must not repoint').toBe(freshManifest);
      if (m2) {
        expect(isSessionManifest(m2), 'F5-before-save must not pull in a session').toBeFalsy();
      }

      // 3. Save. The save repoints urls= to the response resumeUrl.
      const sessionsBefore = await countSessionItems(page.request, g);
      const resumeUrl1 = await remoteSave(view);
      await shot(view, info, `${gesture}-3-after-save`);
      expect(resumeUrl1, 'save response carried no resumeUrl').toBeTruthy();
      expect(urlsParam(view), 'urls= must repoint to the save resumeUrl').toBe(resumeUrl1);
      if (gesture !== 'single-item') {
        // Folder-scoped save creates a NEW session.volview.zip item.
        const sessionsAfter = await countSessionItems(page.request, g);
        expect(sessionsAfter, 'folder save should add a session item').toBeGreaterThan(sessionsBefore);
      }

      // 4. F5 after save -> the just-made save reloads (resume).
      const m4 = await reloadCapturingManifest(view);
      await shot(view, info, `${gesture}-4-f5-resumes-save`);
      expect(urlsParam(view), 'F5-after-save must stay on the resumeUrl').toBe(resumeUrl1);
      if (m4) {
        expect(isSessionManifest(m4), `resume manifest should name the saved session: ${resourceNames(m4)}`).toBeTruthy();
      }

      if (gesture === 'filter' || gesture === 'checked') {
        // Redoing the SAME gesture from scratch: a filter pick resumes its
        // matching save, but checking raw images is the "start fresh" gesture
        // even when exactly this selection was just saved — resume rides only
        // on the repointed resumeUrl (F5 above), never on a new checked launch.
        const shouldResume = gesture === 'filter';
        const reopenDriver = await page.context().newPage();
        const reopened = await launchGesture(reopenDriver, g, gesture);
        expect(
          isSessionManifest(reopened.manifest),
          shouldResume
            ? `reopening ${gesture} should resume matching work: ${resourceNames(reopened.manifest)}`
            : `reopening checked raw picks must start fresh: ${resourceNames(reopened.manifest)}`
        ).toBe(shouldResume);
        if (reopened.view !== reopenDriver) await reopened.view.close();
        await reopenDriver.close();
      }

      // 5. Save again -> F5 -> the SECOND save reloads.
      const resumeUrl2 = await remoteSave(view);
      await shot(view, info, `${gesture}-5-after-second-save`);
      expect(resumeUrl2, 'second save carried no resumeUrl').toBeTruthy();
      expect(urlsParam(view)).toBe(resumeUrl2);
      await reloadCapturingManifest(view);
      await shot(view, info, `${gesture}-6-f5-resumes-second-save`);
      expect(urlsParam(view), 'F5 after the second save must stay on the second resumeUrl').toBe(resumeUrl2);
    });
  }

  test('fresh restart via checked raw images: starts clean, then F5 resumes the NEW save', async ({ page }, info) => {
    // The user's "start over" workflow: an older save exists, they check the
    // raw images to restart clean, annotate, save, and F5 must reload the NEW
    // save (via the repointed resumeUrl) — not the older session, not fresh.
    // Seed the older session (real UI gesture).
    const seed = await launchGesture(page, g, 'checked');
    const olderResume = await remoteSave(seed.view);
    expect(olderResume, 'seeding save carried no resumeUrl').toBeTruthy();
    await seed.view.close();

    // Fresh restart: redoing the checked-raw gesture ignores the older save.
    const restart = await launchGesture(page, g, 'checked');
    const view = restart.view;
    await shot(view, info, 'restart-1-fresh-despite-older-save');
    expect(urlsParam(view), 'checked raw restart must open fresh').toBe(restart.freshManifest);
    if (restart.manifest) {
      expect(
        isSessionManifest(restart.manifest),
        `restart must not resume the older save: ${resourceNames(restart.manifest)}`
      ).toBeFalsy();
    }

    // Save the restarted session (the annotate-then-save gesture).
    const newResume = await remoteSave(view);
    await shot(view, info, 'restart-2-after-save');
    expect(newResume, 'restart save carried no resumeUrl').toBeTruthy();
    expect(newResume, 'the new save must mint its own session item').not.toBe(olderResume);

    // F5 picks up the LATEST save.
    const m2 = await reloadCapturingManifest(view);
    await shot(view, info, 'restart-3-f5-resumes-new-save');
    expect(urlsParam(view), 'F5 must reload the new save, not the older one').toBe(newResume);
    if (m2) {
      expect(
        isSessionManifest(m2),
        `F5 should load the saved session: ${resourceNames(m2)}`
      ).toBeTruthy();
    }
  });

  test('bare folder-open resumes the newest session (after a folder-scoped save)', async ({ page }, info) => {
    // Guarantee a session exists in the folder: launch the checked gesture and save.
    const seed = await launchGesture(page, g, 'checked');
    const seededResume = await remoteSave(seed.view);
    expect(seededResume).toBeTruthy();
    await seed.view.close();

    // Now the BARE folder-open (the button with nothing checked) must resume
    // the newest session, not raw images.
    const bare = await launchGesture(page, g, 'bare-folder');
    await shot(bare.view, info, `bare-folder-resumes-newest`);
    expect(urlsParam(bare.view)).toBe(bare.freshManifest); // bare folder manifest route
    if (bare.manifest) {
      expect(
        isSessionManifest(bare.manifest),
        `bare open should resume a session: ${resourceNames(bare.manifest)}`
      ).toBeTruthy();
    }
  });

  test('checking an OLDER session opens THAT save, not the newest', async ({ page }, info) => {
    // The back-in-history gesture. launch.py promises an explicitly checked
    // session item "opens through to EXACTLY that session ... never re-match it
    // to a newer sibling save" — which only means anything when a newer save
    // exists to be wrongly substituted, so make two.
    const checked = launchUrl(g, 'checked');
    await gotoCapturingManifest(page, checked.url);

    const olderResume = await remoteSave(page);
    expect(olderResume, 'first save carried no resumeUrl').toBeTruthy();
    const newerResume = await remoteSave(page);
    expect(newerResume, 'second save carried no resumeUrl').toBeTruthy();
    expect(newerResume, 'the second save must mint its own session item').not.toBe(olderResume);

    const idOf = (resumeUrl: string) => resumeUrl.split('/item/')[1].split('/volview')[0];
    const olderId = idOf(olderResume);
    const newerId = idOf(newerResume);
    // Discriminate by FILE id, not name: girder dedupes the colliding item
    // names but the file inside each keeps the original "session.volview.zip",
    // so names cannot tell the two saves apart in a manifest.
    const olderFileId = await firstFileId(page.request, g.token, olderId);
    const newerFileId = await firstFileId(page.request, g.token, newerId);
    expect(olderFileId, 'the two saves share a file id').not.toBe(newerFileId);

    // Check ONLY the older session row: a single session item with no folders
    // opens directly, with no "Will open newest VolView session" confirm.
    await page.goto(`${CONFIG.baseURL}/#folder/${g.folderId}`, { waitUntil: 'domcontentloaded' });
    const olderRow = page.locator(`li.g-item-list-entry:has(a[href="#item/${olderId}"])`);
    await expect(olderRow, 'the older session item is not listed in the folder').toBeVisible();
    await olderRow.locator('input.g-list-checkbox').check();

    const popupPromise = page.waitForEvent('popup');
    await page.locator('.open-in-volview').click();
    const popup = await popupPromise;
    await popup.waitForLoadState('domcontentloaded');
    await waitForVolViewReady(popup);
    await shot(popup, info, 'older-session-reopened');

    const urls = urlsParam(popup);
    expect(urls, 'the launch must carry the OLDER session id').toContain(`items=${olderId}`);
    expect(urls, 'the newer session must not be launched').not.toContain(`items=${newerId}`);

    // Read the manifest by the tab's own urls= leg rather than intercepting:
    // the popup can resolve its request before an interceptor could attach.
    const m = await fetchManifest(page.request, g.token, urls);
    expect(isSessionManifest(m), `reopening an older save should resume a session: ${resourceNames(m)}`).toBeTruthy();
    const urlsInManifest = resourceUrls(m).join(' ');
    expect(urlsInManifest, 'the OLDER save must be what loaded').toContain(`/file/${olderFileId}/`);
    expect(urlsInManifest, 'the newest save must NOT be substituted').not.toContain(`/file/${newerFileId}/`);
    await popup.close();
  });

  test('the plugin emits the launch URL the tests synthesize (checked images)', async ({ page }) => {
    // launchUrl() in helpers/girder.ts is a REPLICA of open.js. Every launch
    // test drives that replica, so if the product's URL construction changed
    // underneath them they would all keep passing against a URL the plugin no
    // longer emits. This pins the replica to the real button.
    await page.goto(`${CONFIG.baseURL}/#folder/${g.folderId}`, { waitUntil: 'domcontentloaded' });
    const imageRow = page.locator(`li.g-item-list-entry:has(a[href="#item/${g.itemId}"])`);
    await expect(imageRow).toBeVisible();
    await imageRow.locator('input.g-list-checkbox').check();

    const button = page.locator('.open-in-volview');
    // Also gates on the checkbox handler having re-rendered the href.
    await expect(button).toHaveText(/Open Checked in VolView/);
    const href = await button.getAttribute('href');
    expect(href, 'the open button carries no href').toBeTruthy();

    const actual = new URL(href!, CONFIG.baseURL);
    const expected = new URL(launchUrl(g, 'checked').url);
    expect(actual.pathname, 'VolView dist path').toBe(expected.pathname);
    for (const leg of ['urls', 'names', 'config']) {
      expect(actual.searchParams.get(leg), `${leg}= leg drifted from open.js`).toBe(
        expected.searchParams.get(leg)
      );
    }

    // save= is compared semantically, not textually: the plugin omits an
    // undefined `folders` key where the replica writes an explicit [], and the
    // backend defaults the two the same way.
    const saveLeg = (u: URL) => {
      const save = new URL(u.searchParams.get('save')!, CONFIG.baseURL);
      const linked = JSON.parse(save.searchParams.get('metadata') || '{}').linkedResources || {};
      return { path: save.pathname, items: linked.items || [], folders: linked.folders || [] };
    };
    expect(saveLeg(actual), 'save= leg drifted from open.js').toEqual(saveLeg(expected));
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
});
