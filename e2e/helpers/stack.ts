import { execFileSync } from 'child_process';
import * as path from 'path';
import { APIRequestContext } from '@playwright/test';
import { CONFIG, apiUrl } from './config';

// ---------------------------------------------------------------------------
// Stack lifecycle. The suite assumes a stack that deploy-dev.sh already brought
// up and deployed the developing worktrees onto; it does not manage docker. Two
// checks run in global setup: a fail-fast health probe, then a deploy-receipt
// guard that refuses to run against a stale/wrong deploy.
// ---------------------------------------------------------------------------

const VERSION_URL = apiUrl('/system/version');
const DEPLOY_DEV = '/home/paulhax/src/dsa/deploy-dev.sh';

const BRING_UP_HINT =
  `\nServe the developing code + write the deploy receipt:\n  ${DEPLOY_DEV}\n` +
  `then wait for  curl -f ${VERSION_URL}  to succeed.\n` +
  `A bare stack serves the MAIN checkout + stock VolView; deploy-dev.sh serves\n` +
  `your worktrees, and this harness verifies its receipt.`;

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
// Deploy guard. deploy-dev.sh writes a receipt next to the served SPA recording
// the worktree HEADs it deployed. Refuse to run unless the stack is serving THIS
// girder_volview worktree's current HEAD — so a stale/wrong deploy fails fast and
// loud ("run deploy-dev.sh") instead of surfacing as a confusing mid-test
// assertion (the exact trap an earlier run fell into: it tested the MAIN checkout).
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
        `The stack was not deployed via deploy-dev.sh, so it is serving the MAIN\n` +
        `checkout + stock VolView — not your worktrees. Run:\n  ${DEPLOY_DEV}`
    );
  }

  // The harness lives at <girder_volview worktree>/e2e/helpers, so the worktree
  // root is two dirs up. Prove the stack serves THIS worktree's HEAD.
  const worktreeRoot = path.resolve(__dirname, '..', '..');
  const localGirder = gitHead(worktreeRoot);
  if (localGirder && receipt.girderSha && localGirder !== receipt.girderSha) {
    throw new Error(
      `[e2e] deploy is stale: the stack serves girder_volview ${receipt.girderShort} ` +
        `but this worktree is at ${localGirder.slice(0, 9)}.\nRe-run ${DEPLOY_DEV}.`
    );
  }

  // eslint-disable-next-line no-console
  console.log(
    `[e2e] deploy receipt OK — girder ${receipt.girderShort}, VolView ${receipt.volviewShort}.`
  );
}
