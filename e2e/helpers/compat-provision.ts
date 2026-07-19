import { execFileSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { APIRequestContext } from '@playwright/test';
import { CONFIG, apiUrl } from './config';
import { readJson } from './http';
import { makeNrrd } from './nrrd';
import { authenticate, createFolderUnder, uploadFile, deleteFolder } from './provision';
import { CompatState } from './compat-state';

// Compat provisioning: one run-root folder holding an nrrd/ subfolder (the
// synthetic images the normal suite also uses) and a dicom/ subfolder (real
// IDC slices plain-uploaded by `seed.py seed-small`, plus an item-list
// config that makes the folder filter/group on meta.dicom.*).

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const SEED_CLI = path.resolve(__dirname, '..', 'seed', 'seed.py');
const DICOM_LI_CONFIG = path.resolve(__dirname, '..', 'fixtures', 'dicom.large_image_config.yaml');

function seedSmallDicom(folderId: string): void {
  // eslint-disable-next-line no-console
  console.log(`[compat] seeding small DICOM tier into folder ${folderId} (uv run seed.py)`);
  execFileSync('uv', ['run', SEED_CLI, 'seed-small', '--folder-id', folderId, '--slices', '12'], {
    cwd: REPO_ROOT,
    stdio: 'inherit',
    env: {
      ...process.env,
      GIRDER_URL: CONFIG.baseURL,
      DSA_ADMIN_USER: CONFIG.user,
      DSA_ADMIN_PASS: CONFIG.pass,
    },
  });
}

async function listItems(
  request: APIRequestContext,
  token: string,
  folderId: string
): Promise<Array<{ _id: string; name: string }>> {
  const res = await request.get(apiUrl(`/item?folderId=${folderId}&limit=1000`), {
    headers: { 'Girder-Token': token },
  });
  return readJson(res, `list items of ${folderId}`);
}

// Probe for the optional devkit tier: the trial folder of the "VolView Devkit"
// collection, when the full devkit has been seeded.
export async function findDevkitTrialFolder(
  request: APIRequestContext,
  token: string
): Promise<string | undefined> {
  const collRes = await request.get(
    apiUrl(`/collection?text=${encodeURIComponent('VolView Devkit')}&limit=10`),
    { headers: { 'Girder-Token': token } }
  );
  const collections: Array<{ _id: string; name: string }> = await collRes.json();
  const devkit = collections.find?.((c) => c.name === 'VolView Devkit');
  if (!devkit) return undefined;
  const folderRes = await request.get(
    apiUrl(`/folder?parentType=collection&parentId=${devkit._id}&name=trial`),
    { headers: { 'Girder-Token': token } }
  );
  const folders: Array<{ _id: string }> = await folderRes.json();
  return folders?.[0]?._id;
}

export async function provisionCompat(
  request: APIRequestContext,
  deployed: { mainGirderSha: string; mainVolviewSha: string }
): Promise<CompatState> {
  const { token, userId } = await authenticate(request);

  const runId = `${Date.now()}-${Math.floor(Math.random() * 1e4)}`;
  const runRootFolderId = await createFolderUnder(
    request,
    token,
    'user',
    userId,
    `girder-volview-compat-${runId}`
  );
  const nrrdFolderId = await createFolderUnder(request, token, 'folder', runRootFolderId, 'nrrd');
  const singleFolderId = await createFolderUnder(request, token, 'folder', runRootFolderId, 'single');
  const dicomFolderId = await createFolderUnder(request, token, 'folder', runRootFolderId, 'dicom');

  const a = await uploadFile(request, token, nrrdFolderId, 'synthetic-a.nrrd', makeNrrd({ variant: 0 }));
  const b = await uploadFile(request, token, nrrdFolderId, 'synthetic-b.nrrd', makeNrrd({ variant: 1 }));
  const c = await uploadFile(request, token, singleFolderId, 'synthetic-c.nrrd', makeNrrd({ variant: 0 }));

  seedSmallDicom(dicomFolderId);
  await uploadFile(
    request,
    token,
    dicomFolderId,
    '.large_image_config.yaml',
    fs.readFileSync(DICOM_LI_CONFIG)
  );

  const dicomItems = await listItems(request, token, dicomFolderId);
  const dicomImages = dicomItems.filter((it) => it.name.endsWith('.dcm'));
  if (dicomImages.length === 0) {
    throw new Error('[compat] seed-small uploaded no .dcm items');
  }

  const devkitTrialFolderId = await findDevkitTrialFolder(request, token);

  return {
    createdAt: new Date().toISOString(),
    mainGirderSha: deployed.mainGirderSha,
    mainVolviewSha: deployed.mainVolviewSha,
    runRootFolderId,
    nrrdFolderId,
    singleFolderId,
    dicomFolderId,
    itemIds: {
      [nrrdFolderId]: [a.itemId, b.itemId],
      [singleFolderId]: [c.itemId],
      [dicomFolderId]: dicomImages.map((it) => it._id),
    },
    itemNames: {
      [nrrdFolderId]: [a.itemName, b.itemName],
      [singleFolderId]: [c.itemName],
      [dicomFolderId]: dicomImages.map((it) => it.name),
    },
    token,
    provisioned: true,
    dicomSeeded: true,
    devkitTrialFolderId,
    gestures: [],
  };
}

export async function teardownCompat(request: APIRequestContext, state: CompatState): Promise<void> {
  let token = state.token;
  try {
    token = (await authenticate(request)).token;
  } catch {
    /* use the stored token */
  }
  await deleteFolder(request, token, state.runRootFolderId);
}
