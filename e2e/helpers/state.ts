import * as fs from 'fs';
import * as path from 'path';

// ---------------------------------------------------------------------------
// The provisioned-state file bridges the Playwright global setup (which creates
// the test folder + images) to the tests and the global teardown. It is
// gitignored and rewritten on every run.
// ---------------------------------------------------------------------------
export type E2eState = {
  // Folder the gestures launch against.
  folderId: string;
  // The synthetic image items in that folder.
  itemIds: string[];
  itemNames: string[];
  // An admin session token captured at provision time (teardown/delete reuse).
  token: string;
  // True when THIS harness created the folder (teardown should delete it).
  provisioned: boolean;
};

export const STATE_PATH = path.resolve(__dirname, '..', '.e2e-state.json');

export function writeState(state: E2eState): void {
  fs.writeFileSync(STATE_PATH, JSON.stringify(state, null, 2), 'utf8');
}

export function readState(): E2eState | undefined {
  try {
    return JSON.parse(fs.readFileSync(STATE_PATH, 'utf8')) as E2eState;
  } catch {
    return undefined;
  }
}

export function clearState(): void {
  try {
    fs.unlinkSync(STATE_PATH);
  } catch {
    /* already gone */
  }
}
