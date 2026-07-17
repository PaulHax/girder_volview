import { getApiRoot } from "@girder/core/rest";

const openButton = `<a class="btn btn-sm btn-primary open-in-volview hidden" style="margin-left: 10px" role="button">
                                <i class="icon-link-ext"></i>Open in VolView</a>`;

export function addButton($el, parentSelector) {
    const parent = $el.find(parentSelector);
    if (!parent.length) {
        console.warn(
            `Tried to add VolView button, but parent element not found with selector: ${parentSelector}`
        );
        return;
    }
    parent.prepend(openButton);
    const button = $el.find(".open-in-volview")[0];
    return button;
}

const volViewPath = `static/built/plugins/volview/index.html`;

// ---------------------------------------------------------------------------
// Launch URL construction. Three "ordinary" legs (SAVE-LOAD-RESTORE-SPEC):
//
// - `urls=` points at the plain manifest route: a specific pick (single item /
//   checked / filter) loads exactly what was picked (fresh); a bare folder-open
//   resumes the folder's newest session.volview.zip, else its raw images;
// - `save=` points at the ordinary session-zip save route -- item-scoped for a
//   single item, folder-scoped with a linkedResources `metadata=` for checked /
//   filter (which names the saved session). The backend returns a `resumeUrl`
//   the client repoints its `urls=` at, so a later F5 reloads the just-made save;
// - `config=` carries the folder's VolView config (which the backend augments
//   with the processing-provider block). VolView recognizes config BY SHAPE from
//   any import channel and gates provider registration on the provider's origin
//   being same-origin (see docs/processing_origin_gate.md) -- `config=` is just
//   the launch leg that delivers it, so without it the Analysis tab never
//   appears here. Folder-scoped because the config route is.
// ---------------------------------------------------------------------------

function configParam(folderId) {
    const configUrl = `/${getApiRoot()}/folder/${folderId}/volview_config/config.json`;
    return `&config=${encodeURIComponent(configUrl)}`;
}

export function openItemURL(item) {
    const itemRoute = `/${getApiRoot()}/item/${item.id}`;
    // Item-scoped save: the session.volview.zip is stuffed into this item.
    const saveParam = `&save=${itemRoute}/volview`;
    const manifestUrl = `${itemRoute}/volview`;
    const downloadParams = `&names=[manifest.json]&urls=${encodeURIComponent(manifestUrl)}`;
    const newTabUrl = `${volViewPath}?${saveParam}${downloadParams}${configParam(
        item.get("folderId")
    )}`;
    return newTabUrl;
}

export function openItem(item) {
    window.open(openItemURL(item), "_blank").focus();
}

function resourcesToDownloadParams(folderId, resources) {
    const items = (resources.item || []).join(",");
    const folders = (resources.folder || []).join(",");
    const manifestUrl = `/${getApiRoot()}/folder/${folderId}/volview?folders=${folders}&items=${items}`;
    return `&names=[manifest.json]&urls=${encodeURIComponent(manifestUrl)}`;
}

export function openResourcesURL(folder, resources) {
    const folderRoute = `/${getApiRoot()}/folder/${folder.id}`;
    const metaData = {
        linkedResources: {
            items: resources.item,
            folders: resources.folder,
        },
    };
    const saveParam = `&save=${folderRoute}/volview?metadata=${encodeURIComponent(
        JSON.stringify(metaData)
    )}`;
    const downloadParams = resourcesToDownloadParams(folder.id, resources);
    const newTabUrl = `${volViewPath}?${saveParam}${downloadParams}${configParam(
        folder.id
    )}`;
    return newTabUrl;
}

export function openResources(folder, resources) {
    window.open(openResourcesURL(folder, resources), "_blank").focus();
}

export function groupingFilterForItem(item) {
    const groups = (item.get('meta') || {})._grouping || {};
    const filter = {};
    (groups.keys || []).forEach((key, idx) => {
        if ((groups.values || [])[idx] !== undefined) {
            filter[key] = groups.values[idx];
        }
    });
    return filter;
}

function volViewURLWithFilter(folderId, filterPayload) {
    const folderRoute = `/${getApiRoot()}/folder/${folderId}`;
    const metaData = { linkedResources: { filter: filterPayload } };
    const saveParam = `&save=${folderRoute}/volview?metadata=${encodeURIComponent(
        JSON.stringify(metaData)
    )}`;
    const manifestUrl = `/${getApiRoot()}/folder/${folderId}/volview?filters=${encodeURIComponent(
        JSON.stringify(filterPayload)
    )}`;
    const downloadParams = `&names=[manifest.json]&urls=${encodeURIComponent(manifestUrl)}`;
    return `${volViewPath}?${saveParam}${downloadParams}${configParam(folderId)}`;
}

export function openGroupedItemURL(item, folder) {
    const folderId = folder ? folder.id : item.get('folderId');
    return volViewURLWithFilter(folderId, groupingFilterForItem(item));
}

export function openCheckedGroupedURL(folder, filterList) {
    return volViewURLWithFilter(folder.id, filterList);
}

export function openCheckedGrouped(folder, filterList) {
    window.open(openCheckedGroupedURL(folder, filterList), "_blank").focus();
}
