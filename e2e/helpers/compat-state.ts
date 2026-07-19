import * as fs from 'fs';
import * as path from 'path';

// State bridging the compat CAPTURE phase (run against a main deploy) to the
// VERIFY phase (run against this branch's deploy, after a redeploy in between).
// Unlike .e2e-state.json this file must survive across Playwright invocations,
// so it has its own lifecycle: capture setup writes it, verify teardown deletes
// it (unless COMPAT_KEEP=1).

export type RulerRecord = {
  // The rendered measurement, e.g. "40.00mm" — world coordinates live in the
  // session zip, so a faithful restore reproduces this text exactly.
  lengthText: string;
};

// Semantic summary of a session zip's manifest.json — migration-tolerant
// (counts and presence, not raw JSON equality).
export type ZipSummary = {
  rulerCount: number;
  segmentGroupCount: number;
  // Size of the largest segment-group archive entry: painted voxels make it
  // decidedly non-trivial, an empty labelmap does not.
  segmentGroupDataBytes: number;
  hasLayers: boolean;
  version?: string;
};

export type GestureId =
  | 'checked-nrrd'
  | 'filtered-dicom'
  | 'study-layered'
  | 'single-item'
  | 'devkit-study';

export type LaunchDescriptor =
  | { via: 'checked-items'; itemIds: string[] }
  // Grouped rows matched by the conjunction of cell texts, optionally after
  // narrowing with the filter box.
  | { via: 'checked-rows'; rows: string[][]; filterText?: string }
  | { via: 'item-page'; itemId: string }
  | { via: 'row-nav'; rowTexts: string[] };

export type CapturedGesture = {
  id: GestureId;
  folderId: string;
  launch: LaunchDescriptor;
  // Folder-scoped saves mint a session item (absent for single-item saves,
  // where the zip lands inside the launched item).
  sessionItemId?: string;
  sessionItemName?: string;
  expected: {
    datasetNames: string[];
    rulers: RulerRecord[];
    segmentGroupNames: string[];
    petLayer: boolean;
    zip: ZipSummary;
  };
};

export type CompatState = {
  createdAt: string;
  mainGirderSha: string;
  mainVolviewSha: string;
  runRootFolderId: string;
  nrrdFolderId: string;
  // The single-item gesture gets its own folder so its in-item session zip can
  // never shadow the nrrd folder's newest-session resume ordering.
  singleFolderId: string;
  dicomFolderId: string;
  itemIds: Record<string, string[]>;
  itemNames: Record<string, string[]>;
  token: string;
  provisioned: boolean;
  dicomSeeded: boolean;
  devkitTrialFolderId?: string;
  gestures: CapturedGesture[];
};

export const COMPAT_STATE_PATH = path.resolve(__dirname, '..', '.compat-state.json');

export function writeCompatState(state: CompatState): void {
  fs.writeFileSync(COMPAT_STATE_PATH, JSON.stringify(state, null, 2), 'utf8');
}

export function readCompatState(): CompatState | undefined {
  try {
    return JSON.parse(fs.readFileSync(COMPAT_STATE_PATH, 'utf8')) as CompatState;
  } catch {
    return undefined;
  }
}

export function clearCompatState(): void {
  try {
    fs.unlinkSync(COMPAT_STATE_PATH);
  } catch {
    /* already gone */
  }
}

// Capture specs append gestures one test at a time; read-modify-write keeps the
// file the single source of truth across the serial worker.
export function appendGesture(gesture: CapturedGesture): void {
  const state = readCompatState();
  if (!state) throw new Error('[compat] no state file — did capture setup run?');
  state.gestures = [...state.gestures.filter((g) => g.id !== gesture.id), gesture];
  writeCompatState(state);
}
