import { request as playwrightRequest, FullConfig } from '@playwright/test';
import { readState, clearState } from './helpers/state';
import { authenticate, deleteFolder } from './helpers/provision';

export default async function globalTeardown(_config: FullConfig): Promise<void> {
  const state = readState();
  if (!state) return;

  if (state.provisioned) {
    const request = await playwrightRequest.newContext({ ignoreHTTPSErrors: true });
    try {
      // The setup token may have been invalidated, so re-auth when possible.
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
