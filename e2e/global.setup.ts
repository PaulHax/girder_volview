import { request as playwrightRequest, FullConfig } from '@playwright/test';
import { healthCheck, verifyDeployedHeads } from './helpers/stack';
import { provision } from './helpers/provision';
import { writeState } from './helpers/state';

// ---------------------------------------------------------------------------
// Playwright globalSetup — self-provisions everything the tests need:
//   1. fail-fast health check                       (girder reachable)
//   2. deploy-receipt guard                         (stack serves THIS worktree)
//   3. authenticate + create a fresh public folder + upload synthetic images
//   4. persist { folderId, itemIds, itemNames, token, provisioned } to
//      .e2e-state.json
// The tests + teardown read that state file; nothing has to be hand-created.
// ---------------------------------------------------------------------------
export default async function globalSetup(_config: FullConfig): Promise<void> {
  const request = await playwrightRequest.newContext({ ignoreHTTPSErrors: true });
  try {
    await healthCheck(request);
    // Refuse to run against a stale/wrong deploy — the stack must be serving THIS
    // worktree's code (deploy receipt, see README "Prerequisites"), or every
    // gesture would silently test a stale checkout + stock VolView.
    await verifyDeployedHeads(request);
    const state = await provision(request);
    writeState(state);
    // eslint-disable-next-line no-console
    console.log(
      `[e2e] provisioned folder ${state.folderId} with ${state.itemIds.length} image(s): ` +
        state.itemNames.join(', ')
    );
  } finally {
    await request.dispose();
  }
}
