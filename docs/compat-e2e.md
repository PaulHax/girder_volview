# Backwards-compatibility e2e (sessions saved by main)

Proves that `session.volview.zip` files saved by an **older** girder_volview +
VolView client still open, restore their content, and re-save correctly on the
current branch. Sessions are generated live each run: the harness deploys the
baseline, drives the real girder UI to save sessions through its client,
redeploys the current worktree, then verifies.

```
e2e/scripts/compat.sh
 ├─ materialize-baseline.sh                          # git archive <pinned sha> -> e2e/.compat/
 ├─ script/deploy <baseline export> main             # baseline backend + client
 ├─ playwright --project capture                     # save sessions on the baseline
 ├─ script/deploy <this worktree> just-jobs          # branch backend + client
 └─ playwright --project verify                      # sessions must restore
```

Neither the old sources nor the session zips they produce are committed. Both
are reproducible from a sha, so the repo stores a pointer
(`e2e/compat-baseline.json`) and the harness recreates the rest into the
gitignored `e2e/.compat/` on demand.

Girder's mongo volume survives the redeploy (script/deploy only recreates the
girder container's code), so the folders and sessions captured in step 2 are
still there for step 4. The bridge between the two playwright invocations is
`e2e/.compat-state.json` (gitignored): session item ids, launch descriptors,
and the expected content per gesture.

## What each gesture proves

| Gesture | Launch (real girder UI, on the baseline) | Content saved | Verified on the branch |
|---|---|---|---|
| `single-item` | item page → Open in VolView | ruler | item manifest serves the session; measurements exact; re-save |
| `checked-nrrd` | check 2 NRRD rows → Open Checked | ruler + painted segment group | bare-folder open resumes it; checking the session row opens exactly it; groups + measurements survive; re-save |
| `filtered-dicom` | filter box narrows to one patient → check series row → Open | ruler | replaying the same filter gesture resumes the matching `session.<filter>.volview.zip` |
| `study-layered` | check CT + PET series rows of one study → Open | PET layered over CT + ruler | replay resumes; the layer survives restore and re-save |
| `devkit-study` (optional) | patient → study drill-down in the devkit collection | PET layer + ruler | replay resumes; skipped unless `seed.py seed` has run |

Content checks are semantic, not pixel-based: ruler measurement text must match
exactly (world coordinates live in the zip), segment-group names must survive,
the re-saved zip's manifest must keep rulers/segment groups/layers (schema
migrations are fine; content loss is not). Screenshots are attached to the
report as evidence, never asserted on.

The small DICOM tier is real IDC data (ACRIN NSCLC FDG-PET/CT, CC-BY): the
devkit's pinned `patient-01/study-01/{CT,PET}` + `patient-02/study-01/CT`
series at 12 slices each, plain-uploaded by `seed.py seed-small` with
`meta.dicom.*` set, plus `e2e/fixtures/dicom.large_image_config.yaml` so the
folder groups into series rows with a filter box.

## Running it

One-time setup:

```bash
cd e2e && npm ci && npm run install-browser
uv run seed/seed.py fetch --small     # small DICOM cache (also run by compat.sh)
```

Machine-specific paths live in a gitignored `.env` at the repo root:

```bash
cp .env.example .env && $EDITOR .env      # DSA_DEVOPS, VOLVIEW_ROOT, ...
```

The stack must already be running (see `docs/development.md`); `script/deploy`
only swaps the code it serves. The upstream DSA checkout needs no
edits: `script/girder-volview.override.yml` is layered last and re-points the
`/opt/girder_volview` mount at the worktree (or compat baseline) being deployed,
which is the only change this repo needs to make to the stack. Compose merges a
service's volume list by mount target, so it replaces upstream's hardcoded
`../../../girder_volview` rather than conflicting with it. What the harness needs beyond `.env`:

- **The baseline backend** — no checkout required. It is exported from this
  repo's own history at the sha pinned in `e2e/compat-baseline.json`. Override
  for a one-off with `COMPAT_BASELINE_REF=origin/main`, or point at a real git
  checkout with `COMPAT_OLD_CHECKOUT` + `COMPAT_OLD_SHA`. The checkout's HEAD
  must equal that sha; the deploy derives its receipt directly from git.
- **VolView worktrees** `main` and `just-jobs` under `VOLVIEW_ROOT` (override
  `COMPAT_BASELINE_VOLVIEW` / `COMPAT_BRANCH_VOLVIEW`). The baseline's VolView
  sha and published npm version are recorded in `e2e/compat-baseline.json` so
  the pairing is reproducible off this machine; the branch-side client is
  unpublished and must build from source. Before either deploy, the harness
  requires each checkout's HEAD to equal its manifest pin. A one-off checkout
  override therefore needs a matching `COMPAT_BASELINE_VOLVIEW_SHA` or
  `COMPAT_BRANCH_VOLVIEW_SHA` override too.

To move the baseline forward, resolve the new sha and edit
`e2e/compat-baseline.json`. It is pinned rather than floating so that a red
compat run is bisectable — "did main move, or did I break it?" has an answer.

Full run (two deploys, roughly 10–15 minutes):

```bash
cd e2e && npm run compat
```

Iterating:

```bash
npm run compat:verify-fast    # verify only, no redeploy, keep state for re-runs
npm run compat:capture        # capture half only (deploys main first)
bash scripts/compat.sh --phase capture --skip-deploy   # re-capture, baseline already deployed
bash scripts/compat.sh --link                          # fast client deploys (docker cp, no npm pack)
npm run compat:clean          # remove materialized baselines (handles root-owned residue)
npm run report                # html report of the last phase
```

The capture phase refuses to start if `.compat-state.json` already exists —
that means a previous capture was never verified or torn down. Either finish it
(`npm run compat:verify`) or delete the state file and the
`girder-volview-compat-<runId>` folder it names (the file records
`runRootFolderId` and a token).

Optional full-devkit tier: seed the "VolView Devkit" collection first
(`e2e/seed/README.md`: MinIO + `fetch` + `stage` + `seed`); the `devkit-study`
gesture then runs automatically and cleans up the session items it mints.

## Guards

- `verifyDeployedHeads` (e2e/helpers/stack.ts) requires the deployed backend and
  client shas to equal the intended checkouts. The capture phase runs against
  the baseline pair on purpose, so `compat.sh` passes both
  `E2E_EXPECT_GIRDER_SHA=<baseline sha>` and
  `E2E_EXPECT_VOLVIEW_SHA=<baseline client sha>`; verify passes both branch
  pins. Outside compat, the backend expectation is this worktree and the client
  expectation is the receipt's still-live VolView checkout. A missing sha or
  unreadable expected checkout is a hard failure rather than a silent pass.
- The guard also hashes the served `index.html` and, when the receipt worktrees
  are locally accessible, compares both the built client and Python source tree
  to the receipt. Moving a checkout or changing code after deploy therefore
  requires a fresh deploy rather than producing a false green run.
- `script/deploy` writes the receipt only after it has proven the served SPA
  and the mounted backend match what it deployed, so a receipt never certifies
  a deploy that failed partway.
- The baseline export has no `.git`, so its sha is asserted via `--girder-sha`
  rather than derived. That claim is backstopped: the deploy compares the
  mounted backend tree hash against the tree it was handed, and
  `materialize-baseline.sh` is the only thing that writes those trees.
- The normal suite (`npm test`) ignores `tests/compat/` entirely; compat specs
  run only under `compat.playwright.config.ts`, which refuses to start without
  `COMPAT_PHASE`.
- CI does not run this harness, and carries no browser job at all; it is a
  local tool by design.
