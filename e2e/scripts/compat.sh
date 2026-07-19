#!/usr/bin/env bash
set -euo pipefail

# Backwards-compat orchestration:
#
#   1. materialize the BASELINE girder_volview from git history (no second
#      checkout required) and deploy it with the baseline VolView
#   2. playwright `capture` project — real-UI gestures, content, saves
#      (E2E_EXPECT_GIRDER_SHA carries the baseline sha past the deploy guard)
#   3. redeploy THIS worktree + its paired VolView
#   4. playwright `verify` project — sessions must restore + re-save
#
# The stack itself (a running docker compose project, dsa-plus by default) must
# already exist; script/deploy only swaps the code it serves. Mongo survives the
# redeploy, so the girder folders/sessions captured in step 2 are still there
# for step 4.
#
# The baseline is a `git archive` export under the gitignored e2e/.compat/,
# pinned by e2e/compat-baseline.json. Neither the old sources nor the session
# zips they produce are ever committed — both are reproducible from a sha.
#
# Usage: compat.sh [--phase all|capture|verify] [--skip-deploy] [--link] [--keep]
#
#   --phase        which half to run (default all)
#   --skip-deploy  don't deploy (the stack already serves the right code)
#   --link         pass --link to script/deploy (fast client copy; default pack)
#   --keep         keep the run folder + state after verify (iteration)
#
# Env overrides: COMPAT_BASELINE_REF (baseline ref instead of the pin),
# COMPAT_NO_FETCH, COMPAT_OLD_CHECKOUT/COMPAT_OLD_SHA, COMPAT_BRANCH_VOLVIEW,
# COMPAT_BASELINE_VOLVIEW, COMPAT_DEPLOY.

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)
E2E="$REPO/e2e"

BASELINE_VOLVIEW=${COMPAT_BASELINE_VOLVIEW:-main}
BRANCH_VOLVIEW=${COMPAT_BRANCH_VOLVIEW:-just-jobs}
DEPLOY=${COMPAT_DEPLOY:-$REPO/script/deploy}

PHASE=all
SKIP_DEPLOY=0
LINK_FLAG=""
KEEP=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --phase) PHASE=$2; shift 2 ;;
        --skip-deploy) SKIP_DEPLOY=1; shift ;;
        --link) LINK_FLAG=--link; shift ;;
        --keep) KEEP=1; shift ;;
        -h|--help) sed -n '/^# Usage:/,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done
case "$PHASE" in all|capture|verify) ;; *) echo "--phase must be all|capture|verify" >&2; exit 2 ;; esac

die() { echo "compat: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
[[ -x $DEPLOY ]] || die "deploy script not found/executable: $DEPLOY (set COMPAT_DEPLOY)"
command -v uv >/dev/null || die "uv is required (seed.py seed-small runs via 'uv run')"

# Unconditional, including under --skip-deploy: the capture phase exports
# E2E_EXPECT_GIRDER_SHA either way, and this is a cache hit that needs no docker.
BASELINE_DIR_SHA=$("$E2E/scripts/materialize-baseline.sh")
MAIN_SHA=$BASELINE_DIR_SHA
BASELINE_DIR="$E2E/.compat/checkout-${MAIN_SHA:0:9}"
[[ -n ${COMPAT_OLD_CHECKOUT:-} ]] && BASELINE_DIR=$COMPAT_OLD_CHECKOUT

BRANCH_SHA=$(git -C "$REPO" rev-parse HEAD)
if [[ $MAIN_SHA == "$BRANCH_SHA" ]]; then
    echo "compat: WARNING — the baseline and this worktree are the same commit; the run is vacuous" >&2
fi

echo "compat: baseline ${MAIN_SHA:0:9} at $BASELINE_DIR (VolView: $BASELINE_VOLVIEW)"
echo "compat: branch   ${BRANCH_SHA:0:9} at $REPO (VolView: $BRANCH_VOLVIEW)"

# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------
run_capture() {
    echo "compat: ensuring the small-tier DICOM cache (fetch --small is idempotent)..."
    uv run "$E2E/seed/seed.py" fetch --small

    if [[ $SKIP_DEPLOY -eq 0 ]]; then
        echo "compat: deploying the baseline..."
        # --girder-sha because the export is a plain tree with no .git to ask.
        "$DEPLOY" $LINK_FLAG --girder-sha "$MAIN_SHA" -- "$BASELINE_DIR" "$BASELINE_VOLVIEW"
    fi
    echo "compat: running capture specs against the baseline (${MAIN_SHA:0:9})..."
    (
        cd "$E2E"
        COMPAT_PHASE=capture E2E_EXPECT_GIRDER_SHA=$MAIN_SHA \
            npx playwright test --config compat.playwright.config.ts --project capture
    )
}

run_verify() {
    if [[ $SKIP_DEPLOY -eq 0 ]]; then
        echo "compat: deploying THIS worktree..."
        "$DEPLOY" $LINK_FLAG "$REPO" "$BRANCH_VOLVIEW"
    fi
    echo "compat: running verify specs against this worktree (${BRANCH_SHA:0:9})..."
    (
        cd "$E2E"
        if [[ $KEEP -eq 1 ]]; then export COMPAT_KEEP=1; fi
        COMPAT_PHASE=verify \
            npx playwright test --config compat.playwright.config.ts --project verify
    )
}

if [[ $PHASE == all || $PHASE == capture ]]; then run_capture; fi
if [[ $PHASE == all || $PHASE == verify ]]; then run_verify; fi

echo "compat: done — sessions saved by ${MAIN_SHA:0:9} verified on ${BRANCH_SHA:0:9}."
echo "compat: report: cd e2e && npm run report"
