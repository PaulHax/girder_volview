import { request as playwrightRequest, FullConfig } from '@playwright/test';
import { healthCheck, verifyDeployedHeads, fetchDeployReceipt } from './helpers/stack';
import { provisionCompat } from './helpers/compat-provision';
import { readCompatState, writeCompatState, COMPAT_STATE_PATH } from './helpers/compat-state';

// Phase-switched global setup for the compat suite.
//
//   COMPAT_PHASE=capture  — against the MAIN deploy (E2E_EXPECT_GIRDER_SHA
//                           carries main's sha): provision the run folder ONCE
//                           and write .compat-state.json.
//   COMPAT_PHASE=verify   — against THIS worktree's deploy: require the state
//                           the capture phase left behind.
export default async function compatSetup(_config: FullConfig): Promise<void> {
  const phase = process.env.COMPAT_PHASE;
  if (phase !== 'capture' && phase !== 'verify') {
    throw new Error(
      `[compat] COMPAT_PHASE must be 'capture' or 'verify' (got '${phase ?? ''}'). ` +
        'Run via e2e/scripts/compat.sh.'
    );
  }

  const request = await playwrightRequest.newContext({ ignoreHTTPSErrors: true });
  try {
    await healthCheck(request);
    await verifyDeployedHeads(request);

    if (phase === 'capture') {
      if (readCompatState()) {
        throw new Error(
          `[compat] ${COMPAT_STATE_PATH} already exists — a previous capture was not ` +
            'verified/torn down. Run the verify phase (or delete the state file and its ' +
            'run folder) first.'
        );
      }
      const receipt = await fetchDeployReceipt(request);
      const state = await provisionCompat(request, {
        mainGirderSha: receipt.girderSha || '',
        mainVolviewSha: receipt.volviewSha || '',
      });
      writeCompatState(state);
      // eslint-disable-next-line no-console
      console.log(
        `[compat] capture provisioned: root ${state.runRootFolderId} ` +
          `(nrrd ${state.nrrdFolderId}, dicom ${state.dicomFolderId}) ` +
          `against main girder ${state.mainGirderSha.slice(0, 9)}`
      );
    } else {
      const state = readCompatState();
      if (!state?.provisioned) {
        throw new Error('[compat] no .compat-state.json — run the capture phase first.');
      }
      if (state.gestures.length === 0) {
        throw new Error('[compat] capture recorded no gestures — nothing to verify.');
      }
      // eslint-disable-next-line no-console
      console.log(
        `[compat] verifying ${state.gestures.length} captured gesture(s) from ` +
          `main girder ${state.mainGirderSha.slice(0, 9)}`
      );
    }
  } finally {
    await request.dispose();
  }
}
