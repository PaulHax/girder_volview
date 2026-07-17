import { request as playwrightRequest, FullConfig } from '@playwright/test';
import { readState, clearState } from './helpers/state';
import { authenticate, deleteFolder } from './helpers/provision';

// ---------------------------------------------------------------------------
// Playwright globalTeardown — delete the folder global setup provisioned, then
// clear the state file.
// ---------------------------------------------------------------------------
export default async function globalTeardown(_config: FullConfig): Promise<void> {
  const state = readState();
  if (!state) return;

  if (state.provisioned) {
    const request = await playwrightRequest.newContext({ ignoreHTTPSErrors: true });
    try {
      // Re-auth for a fresh token (the setup token may have been invalidated);
      // fall back to the stored token if re-auth fails.
      let token = state.token;
      try {
        token = (await authenticate(request)).token;
      } catch {
        /* use the stored token */
      }
      await deleteFolder(request, token, state.folderId);
    } finally {
      await request.dispose();
    }
  }

  clearState();
}
