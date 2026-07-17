import { APIRequestContext, BrowserContext, expect } from '@playwright/test';
import { CONFIG, apiUrl } from './config';
import { readState } from './state';

// Re-export the shared deployment config (see helpers/config.ts + README.md).
export { CONFIG };

export type Girder = { token: string; folderId: string; itemId: string; itemName: string };

const api = apiUrl;

// The launch folder the global setup provisioned (from .e2e-state.json).
export function resolveContext(): { folderId: string } {
  const state = readState();
  return { folderId: state?.folderId || '' };
}

// Authenticate against girder (Basic auth) and return the session token. The
// same token is planted as the `girderToken` cookie so VolView's cookie-auth
// manifest/save routes accept the launched tab.
export async function login(request: APIRequestContext): Promise<string> {
  const basic = Buffer.from(`${CONFIG.user}:${CONFIG.pass}`).toString('base64');
  const res = await request.get(api('/user/authentication'), {
    headers: { Authorization: `Basic ${basic}` },
  });
  expect(res.ok(), `girder auth failed (${res.status()}) for ${CONFIG.user}@${CONFIG.baseURL}`).toBeTruthy();
  const body = await res.json();
  const token = body?.authToken?.token;
  expect(token, 'no authToken.token in girder auth response').toBeTruthy();
  return token as string;
}

export async function plantCookie(context: BrowserContext, token: string) {
  const { hostname } = new URL(CONFIG.baseURL);
  await context.addCookies([
    { name: 'girderToken', value: token, domain: hostname, path: '/' },
  ]);
}

// Discover a loadable raw-image item in the folder (skip session zips + job
// outputs).
export async function resolveImageItem(
  request: APIRequestContext,
  token: string,
  folderId: string
): Promise<{ itemId: string; itemName: string }> {
  expect(folderId, 'no launch folder: global setup did not provision one').toBeTruthy();
  const res = await request.get(api(`/item?folderId=${folderId}&limit=100`), {
    headers: { 'Girder-Token': token },
  });
  const items: Array<{ _id: string; name: string; meta?: Record<string, unknown> }> = await res.json();
  const image = items.find(
    (it) =>
      !it.name.endsWith('.volview.zip') &&
      !it.name.endsWith('.volview.json') &&
      !it.meta?.volviewJobOutput
  );
  expect(image, `no loadable raw-image item found in folder ${folderId}`).toBeTruthy();
  return { itemId: image!._id, itemName: image!.name };
}

export async function setup(request: APIRequestContext, context: BrowserContext): Promise<Girder> {
  const token = await login(request);
  await plantCookie(context, token);
  const { folderId } = resolveContext();
  const { itemId, itemName } = await resolveImageItem(request, token, folderId);
  return { token, folderId, itemId, itemName };
}

// How many session.volview.zip items currently sit in the folder (used to prove
// a save created a NEW session item for the folder-scoped gestures).
export async function countSessionItems(request: APIRequestContext, g: Girder): Promise<number> {
  const res = await request.get(api(`/item?folderId=${g.folderId}&limit=1000`), {
    headers: { 'Girder-Token': g.token },
  });
  const items: Array<{ name: string }> = await res.json();
  return items.filter((it) => it.name.endsWith('.volview.zip')).length;
}

// ---------------------------------------------------------------------------
// Launch-URL construction — a faithful replica of the plugin launcher
// (girder_volview/web_client/views/open.js). VolView is served at
// /static/built/plugins/volview/index.html; the three legs (save/urls/config)
// are all ordinary (no ?workspace=true, no volview_workspace/save).
// ---------------------------------------------------------------------------
const VOLVIEW = 'static/built/plugins/volview/index.html';
const enc = encodeURIComponent;

function configLeg(folderId: string): string {
  return `&config=${enc(`/${CONFIG.apiRoot}/folder/${folderId}/volview_config/config.json`)}`;
}

export type Gesture = 'single-item' | 'checked' | 'filter' | 'bare-folder';

// Returns the ABSOLUTE launch URL for a gesture, plus the `urls=` (manifest) leg
// value the tab should carry right after launch (so the test can assert it).
export function launchUrl(
  g: Girder,
  gesture: Gesture,
  opts: { token?: string } = {}
): { url: string; freshManifest: string } {
  const base = `${CONFIG.baseURL}/${VOLVIEW}`;
  const cfg = configLeg(g.folderId);
  const names = `&names=[manifest.json]`;
  // Token-only auth leg: mirrors src/utils/token.ts populateAuthorizationToken()
  // (reads ?token= and sets the Authorization bearer). Present ONLY when a test
  // opts in; a token-only launch plants NO girderToken cookie.
  const tok = opts.token ? `&token=${enc(opts.token)}` : '';

  if (gesture === 'single-item') {
    const route = `/${CONFIG.apiRoot}/item/${g.itemId}`;
    const manifest = `${route}/volview`;
    const save = `&save=${route}/volview`;
    const urls = `&urls=${enc(manifest)}`;
    return { url: `${base}?${save}${names}${urls}${cfg}${tok}`, freshManifest: manifest };
  }
  if (gesture === 'checked') {
    const folderRoute = `/${CONFIG.apiRoot}/folder/${g.folderId}`;
    const meta = { linkedResources: { items: [g.itemId], folders: [] } };
    const save = `&save=${folderRoute}/volview?metadata=${enc(JSON.stringify(meta))}`;
    const manifest = `/${CONFIG.apiRoot}/folder/${g.folderId}/volview?folders=&items=${g.itemId}`;
    const urls = `&urls=${enc(manifest)}`;
    return { url: `${base}?${save}${names}${urls}${cfg}${tok}`, freshManifest: manifest };
  }
  if (gesture === 'filter') {
    const folderRoute = `/${CONFIG.apiRoot}/folder/${g.folderId}`;
    const filter = [{ name: g.itemName }];
    const meta = { linkedResources: { filter } };
    const save = `&save=${folderRoute}/volview?metadata=${enc(JSON.stringify(meta))}`;
    const manifest = `/${CONFIG.apiRoot}/folder/${g.folderId}/volview?filters=${enc(JSON.stringify(filter))}`;
    const urls = `&urls=${enc(manifest)}`;
    return { url: `${base}?${save}${names}${urls}${cfg}${tok}`, freshManifest: manifest };
  }
  // bare-folder: no items/folders/filter -> resumes newest session, else raw
  const folderRoute = `/${CONFIG.apiRoot}/folder/${g.folderId}`;
  const meta = { linkedResources: { items: [], folders: [] } };
  const save = `&save=${folderRoute}/volview?metadata=${enc(JSON.stringify(meta))}`;
  const manifest = `/${CONFIG.apiRoot}/folder/${g.folderId}/volview`;
  const urls = `&urls=${enc(manifest)}`;
  return { url: `${base}?${save}${names}${urls}${cfg}`, freshManifest: manifest };
}

// The item-manifest URL a save's resumeUrl points at (item-scoped for a single
// item; a NEW session item for folder-scoped gestures — captured at runtime).
export function itemManifest(itemId: string): string {
  return `/${CONFIG.apiRoot}/item/${itemId}/volview`;
}
