import { request as playwrightRequest, FullConfig } from '@playwright/test';
import { healthCheck, verifyDeployedHeads } from './helpers/stack';
import { provision } from './helpers/provision';
import { writeState } from './helpers/state';

// Provisions a fresh public folder + synthetic images and persists the state
// the tests and teardown read from `.e2e-state.json`.
export default async function globalSetup(_config: FullConfig): Promise<void> {
  const request = await playwrightRequest.newContext({ ignoreHTTPSErrors: true });
  try {
    await healthCheck(request);
    // The stack must serve THIS worktree's code (deploy receipt); against a
    // stale deploy every gesture would silently test a stale checkout.
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
