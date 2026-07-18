# girder_volview browser e2e (Playwright)

The **whole-shebang** browser test: the VolView client SPA served by girder
driving the `girder_volview` Python backend it opens/saves/runs jobs through.
It proves the client↔backend seam that unit tests on either side can't — the
URL-driven **save → refresh → restore** contract, and the **Jobs apply**
path — against a stack that is serving the **developing code**, not stale/stock
code.

It lives in the plugin repo because the plugin *is* the integration point;
VolView stays ignorant of it. The save/restore behavior under test is described
in the top-level README's "Save / restore round-trip" section.

**Self-provisioning:** you do **not** hand-create any test data. A Playwright
`globalSetup` health-checks the stack, verifies the deploy receipt, authenticates
to girder, creates a fresh public folder, and uploads two synthetic NRRD images
generated in-process. A `globalTeardown` deletes that folder afterward.

**No env-var config.** The deployment config is a committed literal in
`helpers/config.ts` (`baseURL` / `apiRoot` / `user` / `pass`); edit it to point at
a different stack. The suite runs **headless** and fully automated.

## Run

```bash
# 1. Deploy the paired stack (this worktree's backend + the paired VolView
#    dist) and write the deploy receipt — see "Prerequisites" below for the
#    contract your deploy tooling must satisfy.

# 2. Install + run the browser e2e:
cd e2e
npm install
npm run install-browser        # playwright install chromium
npm test
```

That's it. The setup provisions its own folder + images; the teardown removes them.
`npm run report` opens the HTML report (with the per-step screenshots attached);
`npm run typecheck` runs `tsc --noEmit`; `npm run list` compiles + lists the specs
without running.

A manual look is available with `npx playwright test --headed`, but it is not part
of the workflow — every invariant is asserted headlessly.

## What it checks

### `save-load-restore.spec.ts` — per gesture (single-item / checked / filter)

1. **First launch → fresh.** With no matching save yet, the tab's `urls=` is the
   picked manifest and names the picked raw image(s).
2. **F5 before saving → stays fresh.** `urls=` is unchanged and still no session.
3. **Save.** The save `POST`s to `save=` (200) and the tab repoints `urls=` to the
   response `resumeUrl` (folder-scoped gestures also create a new
   `session.volview.zip` item).
4. **F5 after save → resumes.** `urls=` stays on the `resumeUrl` and the manifest
   now names the saved session.
5. **Reopen checked/filter → resumes.** Repeating the original gesture resolves
   the matching saved session rather than silently returning fresh images.
6. **Save → F5 again → the *second* save resumes.**

Plus **bare folder-open resumes the newest session**, **checking a saved session
alongside another item warns and opens the session**, and a fast config-sanity test.

The save/restore **URL + manifest mechanics** are what's asserted (the `urls=`
repoint via `history.replaceState`, the save `POST`/`resumeUrl`, the new-session-item
creation, and the manifest contents). Fully automating VolView's paint tools is out
of scope; the mechanics hold with or without a painted edit.

### `run-and-apply.spec.ts` — the jobs/processing plane

Setup submits an **Otsu** segmentation over REST (`helpers/jobs.ts` — the same
folder + user the tab runs as) and polls it to success; each test then launches
VolView on that folder (the launch carries `config=`, which is what makes the
**Jobs** tab appear, and loads the image as a base so the layer/segment-group
applies are enabled), opens **Jobs → Show results**, and drives one apply action:

- **Add as segment group** → a `new job result` chip in the Annotations
  segment-group list;
- **Add as layer** → a new `[data-testid="layer-opacity-slider"]` in Rendering;
- **Open** → a new image (`.dataset-menu`) in Data;
- each with the `Applied …` success toast.

Job submission is REST (setup), not the multi-step task form — keeping the test
focused on the apply path and folder/user scoping. Complements the Python REST
correlation test (`tests/test_end_to_end_live.py`), which proves the server side.

## How provisioning works

`globalSetup` (`global.setup.ts`) runs before any test:

1. **Health check.** `GET {base}/{apiRoot}/system/version`. If girder is
   unreachable it throws immediately with bring-up instructions — the tests
   never spin on a dead stack.
2. **Deploy-receipt guard.** The deploy step writes `deployed-heads.json` next
   to the served SPA recording the worktree HEADs it deployed (see
   "Prerequisites"). The setup refuses to run unless the stack serves **this**
   worktree's current `girder_volview` HEAD — so a bare/stale stack (a stock
   bring-up serving a different checkout + packaged VolView) fails fast and
   loud instead of silently testing the wrong code.
3. **Provision data.** Authenticates (`GET /user/authentication`, Basic),
   `POST /folder?…&reuseExisting=false&public=true` to create a fresh
   `girder-volview-e2e-<runid>` folder, then uploads `synthetic-a.nrrd` and
   `synthetic-b.nrrd` (16³ int16 gradient volumes made by `helpers/nrrd.ts`) via
   girder's two-step upload (`POST /file` init → `POST /file/chunk`).
4. **Persist.** Writes `{ folderId, itemIds, itemNames, token, provisioned }` to
   `.e2e-state.json` (gitignored). The tests read the folder + item from it.

`globalTeardown` (`global.teardown.ts`) deletes the provisioned folder and clears
the state file.

The synthetic NRRD is a genuine ITK-loadable volume, so `waitForVolViewReady`
passes on it just like a real image.

## Prerequisites

- **A deployed paired stack.** How you stand it up is your business (a
  DSA/girder docker-compose bring-up is the reference environment); what the
  harness requires of it is a contract:
  1. girder reachable at `helpers/config.ts`'s `baseURL` with the
     `helpers/config.ts` admin credentials;
  2. the backend is **this worktree's** `girder_volview` (not a released or
     other checkout);
  3. the served SPA at `static/built/plugins/volview/` is the **paired,
     processing-enabled** VolView build (beware: a plain `girder build`
     re-clobbers it with the pinned npm package, which can drop the save
     button and break steps 3–5);
  4. a **deploy receipt** at
     `{baseURL}/static/built/plugins/volview/deployed-heads.json` — JSON
     written at deploy time next to the served `index.html`:

     ```json
     {
       "girderSha":   "<full HEAD of the deployed girder_volview worktree>",
       "girderShort": "<its short form>",
       "volviewSha":  "<full HEAD of the deployed VolView worktree>",
       "volviewShort": "<its short form>"
     }
     ```

     The guard compares `girderSha` against this worktree's `HEAD` and refuses
     to run on mismatch; the other fields are informational. Extra fields are
     fine. Writing the receipt is the deploy tooling's LAST step, so its
     presence certifies the rest of the contract.
- Node 18+ (developed on Node 22).
- Chromium for Playwright (`npm run install-browser`).

## Notes / caveats

- Selectors and the launched-URL format were derived from the plugin launcher
  (`girder_volview/web_client/views/open.js`) and VolView's own e2e page objects
  (`tests/pageobjects/`), and the save path from `ControlsStrip.vue` →
  `remote-save-state.saveState()`. If the UI drifts, adjust the
  `mdi-content-save-all` save-button locator or the `waitForVolViewReady` signal.
- The harness authenticates via `GET /api/v1/user/authentication` (Basic) and
  plants the returned token as the `girderToken` cookie, so VolView's cookie-auth
  manifest/save routes accept the launched tab.
- Standalone (its own `package.json`); it does not affect the plugin's Python test
  suite or VolView's WebdriverIO suite.

## Files

| File | Role |
|---|---|
| `playwright.config.ts` | wires `globalSetup`/`globalTeardown`, timeouts, screenshots (headless) |
| `global.setup.ts` | health check → deploy-receipt guard → provision → write state |
| `global.teardown.ts` | delete provisioned folder + clear state |
| `helpers/config.ts` | committed literal deployment config + `apiUrl()` |
| `helpers/nrrd.ts` | in-process synthetic NRRD volume generator |
| `helpers/provision.ts` | girder REST: auth, folder create, two-step upload, delete |
| `helpers/state.ts` | read/write/clear `.e2e-state.json` |
| `helpers/stack.ts` | health check + deploy-receipt guard |
| `helpers/girder.ts` | launch-URL construction, login/cookie, item resolution |
| `helpers/jobs.ts` | processing REST: find task, submit, poll to terminal (Otsu) |
| `helpers/volview.ts` | ready signal, `urls=` reader, remote save, jobs-apply UI |
| `tests/save-load-restore.spec.ts` | the save/load/restore F5-lifecycle spec |
| `tests/run-and-apply.spec.ts` | the jobs run + apply-result path spec |
