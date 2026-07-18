import { execFileSync } from 'child_process';
import * as path from 'path';
import { APIRequestContext } from '@playwright/test';
import { CONFIG, apiUrl } from './config';

// ---------------------------------------------------------------------------
// Stack lifecycle. The suite assumes an already-deployed paired stack — this
// worktree's backend plus the paired VolView dist, with a deploy receipt next
// to the served SPA (the contract is documented in e2e/README
// "Prerequisites"); it does not manage docker. Two checks run in global setup:
// a fail-fast health probe, then a deploy-receipt guard that refuses to run
// against a stale/wrong deploy.
// ---------------------------------------------------------------------------

const VERSION_URL = apiUrl('/system/version');

const RECEIPT_HINT =
  'Deploy the paired stack and write the deploy receipt — see e2e/README "Prerequisites".';

const BRING_UP_HINT =
  `\n${RECEIPT_HINT}\n` +
  `Then wait for  curl -f ${VERSION_URL}  to succeed.`;

async function versionReachable(request: APIRequestContext): Promise<boolean> {
  try {
    const res = await request.get(VERSION_URL, { timeout: 10_000 });
    return res.ok();
  } catch {
    return false;
  }
}

// Fail-fast: a single-shot health check with an actionable error if girder is
// down, so the tests never spin against a dead stack.
export async function healthCheck(request: APIRequestContext): Promise<void> {
  if (await versionReachable(request)) return;
  throw new Error(`[e2e] girder is not reachable at ${VERSION_URL}.${BRING_UP_HINT}`);
}

// ---------------------------------------------------------------------------
// Deploy guard. The deploy step writes a receipt next to the served SPA
// recording the worktree HEADs it deployed (the receipt contract is in
// e2e/README "Prerequisites"). Refuse to run unless the stack is serving THIS
// girder_volview worktree's current HEAD — so a stale/wrong deploy fails fast
// and loud instead of surfacing as a confusing mid-test assertion (the exact
// trap an earlier run fell into: it tested a different checkout).
// ---------------------------------------------------------------------------
const RECEIPT_URL = `${CONFIG.baseURL}/static/built/plugins/volview/deployed-heads.json`;

type DeployReceipt = {
  girderSha?: string;
  girderShort?: string;
  volviewSha?: string;
  volviewShort?: string;
};

function gitHead(dir: string): string | null {
  try {
    return execFileSync('git', ['-C', dir, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  } catch {
    return null;
  }
}

export async function verifyDeployedHeads(request: APIRequestContext): Promise<void> {
  let receipt: DeployReceipt;
  try {
    const res = await request.get(RECEIPT_URL, { timeout: 10_000 });
    if (!res.ok()) throw new Error(`HTTP ${res.status()}`);
    receipt = JSON.parse(await res.text());
  } catch (e) {
    throw new Error(
      `[e2e] no deploy receipt at ${RECEIPT_URL} (${(e as Error).message}).\n` +
        `Without a receipt the stack is presumed to serve stock code — not this\n` +
        `worktree. ${RECEIPT_HINT}`
    );
  }

  // The harness lives at <girder_volview worktree>/e2e/helpers, so the worktree
  // root is two dirs up. Prove the stack serves THIS worktree's HEAD.
  const worktreeRoot = path.resolve(__dirname, '..', '..');
  const localGirder = gitHead(worktreeRoot);
  if (localGirder && receipt.girderSha && localGirder !== receipt.girderSha) {
    throw new Error(
      `[e2e] deploy is stale: the stack serves girder_volview ${receipt.girderShort} ` +
        `but this worktree is at ${localGirder.slice(0, 9)}.\n` +
        `Redeploy and refresh the receipt. ${RECEIPT_HINT}`
    );
  }

  // eslint-disable-next-line no-console
  console.log(
    `[e2e] deploy receipt OK — girder ${receipt.girderShort}, VolView ${receipt.volviewShort}.`
  );
}
