import { request as playwrightRequest, FullConfig } from '@playwright/test';
import { readCompatState, clearCompatState } from './helpers/compat-state';
import { teardownCompat } from './helpers/compat-provision';

// The capture phase leaves everything in place for verify; only the verify
// phase cleans up — unless COMPAT_KEEP=1 (iterating on verify).
export default async function compatTeardown(_config: FullConfig): Promise<void> {
  const phase = process.env.COMPAT_PHASE;
  if (phase !== 'verify') return;
  if (process.env.COMPAT_KEEP === '1') {
    // eslint-disable-next-line no-console
    console.log('[compat] COMPAT_KEEP=1 — keeping run folder and state for iteration.');
    return;
  }

  const state = readCompatState();
  if (!state) return;
  if (state.provisioned) {
    const request = await playwrightRequest.newContext({ ignoreHTTPSErrors: true });
    try {
      await teardownCompat(request, state);
    } finally {
      await request.dispose();
    }
  }
  clearCompatState();
}
