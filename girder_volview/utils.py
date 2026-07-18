from datetime import datetime, timezone
from girder import logger
from girder.utility.server import getApiRoot
from girder.constants import AccessType
from girder.models.folder import Folder
from girder.models.item import Item

from .handles import mintFileHandle

SESSION_ZIP_EXTENSION = ".volview.zip"
SESSION_JSON_EXTENSION = ".volview.json"
SESSION_EXTENSIONS = (SESSION_ZIP_EXTENSION, SESSION_JSON_EXTENSION)

# Folder-metadata marker stamped on a job's private output folder at creation.
# Job results take the JOB path only: files inside a marked folder stay durable
# (re-fetched via the job) but are filtered OUT of the launch manifest, so
# VolView's native `loadSegmentations` convention never also grabs them (they
# carry no `source` tag, so the client could not dedup against a double-apply).
# Folder ownership subsumes the old per-item `volviewJobOutput` tag — an item's
# parent folder marker is the single exclusion signal. Same exclusion site as
# session zips (`isLoadableImage`).
JOB_OUTPUT_FOLDER_META_KEY = "volviewJobOutputFolder"

# Item-metadata key marking a staged, non-durable processing input (invisible to
# session history + source listings; deleted at job end or TTL). Nothing
# labelmap-specific rides this tag -- it is the whole staging vocabulary. Defined
# here alongside JOB_OUTPUT_FOLDER_META_KEY (its sibling manifest-exclusion
# marker) so the shared exclusion site ``isTransientStagedFile`` reads it directly
# instead of reaching UP into the backend package; ``backend.inputs`` imports it
# downward.
TRANSIENT_STAGED_META_KEY = "volviewTransient"


def _promoteFilterToList(value):
    """Normalize dict-or-list filter input to a list of dicts.
    Returns None for non-conforming values.
    """
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(v, dict) for v in value):
        return value
    return None


# Session-zip save naming (legacy fallback — permanent). A filter-gesture save is
# named session.<patient/study/series…>.volview.zip so a folder can hold one
# session per filter without collision; a plain/checked save is session.volview.zip.
SAFE_NAME_MAX = 80
SESSION_NAME_MAX = SAFE_NAME_MAX * 3
# Suffix-matched against filter keys so both 'meta.dicom.PatientID' and
# 'dicom.PatientID' get the same canonical position.
PREFERRED_FILTER_SUFFIXES = (
    "PatientID",
    "StudyInstanceUID",
    "SeriesInstanceUID",
)


def safeNameComponent(value):
    safe = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in str(value))
    return safe.strip("._")[:SAFE_NAME_MAX]


def _filterKeyPriority(key):
    keyStr = str(key)
    for index, suffix in enumerate(PREFERRED_FILTER_SUFFIXES):
        if keyStr.endswith(suffix):
            return (0, index, keyStr)
    return (1, 0, keyStr)


def sessionNameFromFilter(linkedFilter, extension):
    filtersList = _promoteFilterToList(linkedFilter)
    if not filtersList:
        return f"session{extension}"
    sortedFilters = sorted(
        filtersList,
        key=lambda d: tuple(sorted(d.items())),
    )
    parts = []
    seen = set()
    for filterDict in sortedFilters:
        for key in sorted(filterDict, key=_filterKeyPriority):
            component = safeNameComponent(filterDict[key])
            if component and component not in seen:
                parts.append(component)
                seen.add(component)
    if not parts:
        return f"session{extension}"
    joined = ".".join(parts)
    if len(joined) > SESSION_NAME_MAX:
        joined = joined[:SESSION_NAME_MAX].rstrip(".")
    return f"session.{joined}{extension}"


# https://github.com/Kitware/VolView/blob/main/src/io/mimeTypes.ts
LOADABLE_EXTENSIONS = (
    # VolView app
    ".json",
    ".zip",
    ".vti",
    ".vtp",
    ".stl",
    # @itk-wasm/image-io
    ".bmp",
    ".dcm",
    ".gipl",
    ".gipl.gz",
    ".hdf5",
    ".jpg",
    ".jpeg",
    ".iwi",
    ".iwi.cbor",
    ".iwi.cbor.zst",
    ".lsm",
    ".mnc",
    ".mnc.gz",
    ".mnc2",
    ".mgh",
    ".mgz",
    ".mgh.gz",
    ".mha",
    ".mhd",
    ".mrc",
    ".nia",
    ".nii",
    ".nii.gz",
    ".hdr",
    ".nrrd",
    ".nhdr",
    ".png",
    ".pic",
    ".tif",
    ".tiff",
    ".vtk",
    ".isq",
    ".aim",
    ".fdf",
)

LOADABLE_MIMES = (
    "application/vnd.unknown.vti",
    "application/vnd.unknown.vtp",
    "model/stl",
    "application/dicom",
    "application/zip",
    "application/json",
    "application/vnd.unknown.gipl",
    "application/x-hdf5",
    "image/jpeg",
    "application/vnd.unknown.lsm",
    "application/vnd.unknown.minc",
    "application/vnd.unknown.mgh",
    "application/vnd.unknown.metaimage",
    "application/vnd.unknown.mrc",
    "application/vnd.unknown.nifti-1",
    "application/vnd.unknown.nrrd",
    "image/png",
    "application/vnd.unknown.biorad",
    "image/tiff",
    "application/vnd.unknown.vtk",
    "application/vnd.unknown.scanco",
    "application/vnd.unknown.fdf",
)


# legacy fallback — permanent
# (also load-bearing for `isLoadableImage` -- shared, never touchable).
def isSessionItem(item):
    return item and any(ext in item["name"] for ext in SESSION_EXTENSIONS)


def isSessionFile(file):
    return file.get("name").endswith(SESSION_EXTENSIONS)


def isTiffFile(file):
    return (
        file.get("name").endswith((".tif", ".tiff"))
        or file.get("mimeType") == "image/tiff"
    )


def isDicomFile(file):
    return (
        file.get("name").endswith(".dcm") or file.get("mimeType") == "application/dicom"
    )


def _parentItemForFile(file, user=None, itemCache=None):
    """Load a file's parent item, optionally reusing a request-scoped cache."""
    itemId = file.get("itemId")
    if not itemId:
        return None
    if itemCache is None:
        return Item().load(itemId, user=user, level=AccessType.READ, exc=False)
    if itemId not in itemCache:
        itemCache[itemId] = Item().load(
            itemId, user=user, level=AccessType.READ, exc=False
        )
    return itemCache[itemId]


def isLoadableFile(file, user=None, itemCache=None):
    if isTiffFile(file) or isDicomFile(file):
        item = _parentItemForFile(file, user, itemCache)
        if item is None:
            return False
        if isTiffFile(file):
            return not item.get("largeImage")
        if item.get("meta", {}).get("dicom", {}).get("Modality", "") == "SM":
            return False

    if file.get("name").endswith(LOADABLE_EXTENSIONS):
        return True

    return file.get("mimeType") in LOADABLE_MIMES


def _loadFolderCached(folderId, folderCache=None):
    """Load a folder (force=True — reads only the ownership marker), with an
    optional request-scoped cache keyed by string id."""
    if not folderId:
        return None
    if folderCache is None:
        return Folder().load(folderId, force=True, exc=False)
    key = str(folderId)
    if key not in folderCache:
        folderCache[key] = Folder().load(folderId, force=True, exc=False)
    return folderCache[key]


def isJobOutputFolderFile(file, user=None, itemCache=None, folderCache=None):
    """Whether ``file`` lives inside a job's private output folder.

    Ownership is FOLDER-level: the file's parent item's parent folder carries the
    ``volviewJobOutputFolder`` marker. Job outputs are excluded from the launch
    manifest (results take the job path only) while staying durable in the folder.
    Best-effort: an absent/unreadable parent item or folder is treated as
    not-a-job-output (fail toward showing the file). NOT deletion machinery — the
    manifest routes fold this in through ``isLoadableImage`` below.
    """
    item = _parentItemForFile(file, user, itemCache)
    folderId = item.get("folderId") if isinstance(item, dict) else None
    folder = _loadFolderCached(folderId, folderCache)
    return bool((folder or {}).get("meta", {}).get(JOB_OUTPUT_FOLDER_META_KEY))


def isJobOutputFolderItem(item, folderCache=None):
    """Whether an item lives directly in a job's private output folder.

    The item-route manifest short-circuit (``launch.downloadManifest``) uses this
    to return an empty manifest for a direct open of a job-output item.
    """
    folderId = item.get("folderId") if isinstance(item, dict) else None
    folder = _loadFolderCached(folderId, folderCache)
    return bool((folder or {}).get("meta", {}).get(JOB_OUTPUT_FOLDER_META_KEY))


def isTransientStagedFile(file, user=None, itemCache=None):
    """Whether ``file`` was staged as a transient processing input.

    A staged input (parent item tagged ``volviewTransient``) is working data
    for a job submission, not launch data — a reload must never surface an
    abandoned staged segmentation as an ordinary image. Best-effort like the
    job-output check: an absent/unreadable parent item means not-transient.
    """
    item = _parentItemForFile(file, user, itemCache)
    return bool((item or {}).get("meta", {}).get(TRANSIENT_STAGED_META_KEY))


# The legacy fallback (permanent) flows through here — the
# ``volviewJobOutputFolder`` manifest exclusion is subsumed, not duplicated.
def isLoadableImage(file, user=None, itemCache=None, folderCache=None):
    if isSessionFile(file):
        return False
    if isJobOutputFolderFile(file, user, itemCache, folderCache):
        return False
    if isTransientStagedFile(file, user, itemCache):
        return False
    return isLoadableFile(file, user, itemCache)


def makeFileDownloadUrl(fileModel):
    """
    Given a file model, return a download URL for the file.

    A thin delegate of :mod:`girder_volview.handles` -- the ONE mint site for
    the proxiable load-handle scheme: the name segment is
    percent-encoded there, so reserved delimiters (``#``, ``?``, spaces) in
    legal Girder file names survive the wire and always parse back.

    :param fileModel: the file model.
    :type fileModel: dict
    :returns: the download URL.
    """
    return mintFileHandle(fileModel["_id"], fileModel["name"])


def _toIso(value):
    """Serialize a datetime to an ISO-8601 UTC string, or ``None``.

    The backend's neutral instants (e.g. a job's ``finishedAt``) travel the wire
    as ISO-8601 UTC strings so the client treats
    them as UTC instants — no client clock, no timezone ambiguity. Girder
    stores naive UTC datetimes, so a naive value is tagged UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


# legacy fallback — permanent (the legacy launch manifest shape).
def filesToManifest(files, folderId):
    fileUrls = [
        {"url": makeFileDownloadUrl(fileEntry[1]), "name": fileEntry[1]["name"]}
        for fileEntry in files
    ]
    configUrl = "/".join(
        (
            "",
            getApiRoot(),
            "folder",
            str(folderId),
            "volview_config",
            ".volview_config.yaml",
        )
    )
    fileUrls.append({"url": configUrl, "name": "config.json"})
    return {"resources": fileUrls}


# legacy fallback — permanent
def sameLevelSessionFile(fileEntry):
    # if file name matches the item name, then Item.fileList has no / in the path
    # example: itemName == session.volview.zip and fileName == session.volview.zip,
    # then path == session.volview.zip
    # example: itemName == "session.volview.zip (1)" and fileName ==
    # session.volview.zip, then path == session.volview.zip (1)/session.volview.zip
    paths = fileEntry[0].split("/")
    rootPath = paths[0]
    itemNameIncludesSession = any(ext in rootPath for ext in SESSION_EXTENSIONS)
    directChildSession = len(paths) <= 2 and itemNameIncludesSession
    return directChildSession and isSessionFile(fileEntry[1])


# legacy fallback — permanent
def filterLinkedSessionItemIds(fileEntries):
    sessionItemIds = {
        fileEntry[1].get("itemId")
        for fileEntry in fileEntries
        if sameLevelSessionFile(fileEntry) and fileEntry[1].get("itemId")
    }
    if not sessionItemIds:
        return set()
    matches = Item().find(
        {
            "_id": {"$in": list(sessionItemIds)},
            "meta.linkedResources.filter": {"$exists": True},
        },
        fields=["_id"],
    )
    return {item["_id"] for item in matches}


# legacy fallback — permanent: the newest-zip selection rule, the
# session half of the legacy open-through rung.
def newestSessionFile(fileEntries, includeFilterLinkedSessions=True):
    fileEntries = list(fileEntries)
    excludedItemIds = (
        set()
        if includeFilterLinkedSessions
        else filterLinkedSessionItemIds(fileEntries)
    )
    sessions = [
        fileEntry
        for fileEntry in fileEntries
        if sameLevelSessionFile(fileEntry)
        and fileEntry[1].get("itemId") not in excludedItemIds
    ]
    if not sessions:
        return None
    return max(sessions, key=lambda file: file[1].get("created"))


# legacy fallback — permanent: newest zip wins, else loadable images
# (the item-route explicit session open-through keeps the image half).
def singleVolViewZipOrImageFiles(
    fileEntries,
    user=None,
    includeFilterLinkedSessions=True,
    itemCache=None,
    folderCache=None,
):
    if itemCache is None:
        itemCache = {}
    if folderCache is None:
        folderCache = {}
    fileEntries = list(fileEntries)
    newestSession = newestSessionFile(
        fileEntries, includeFilterLinkedSessions=includeFilterLinkedSessions
    )
    if newestSession is not None:
        return [newestSession]
    return [
        fileEntry
        for fileEntry in fileEntries
        if isLoadableImage(fileEntry[1], user, itemCache, folderCache)
    ]


def idStringToIdList(idString):
    if len(idString) == 0:
        return []
    return idString.split(",")


def getFiles(model, docs):
    # Skip docs that did not load (a stale/deleted/inaccessible id makes
    # loadModels yield None); fileList(None) would dereference None["_id"] and
    # 500. Mirrors getNewestDoc's `if doc` guard.
    fileLists = [
        model().fileList(doc, subpath=False, data=False) for doc in docs if doc
    ]
    # flatten
    files = [file for fileList in fileLists for file in fileList]
    return files


def loadModels(user, model, docIds, level=AccessType.READ):
    return [model().load(id, level=level, user=user) for id in docIds]


def normalizeLinkedResources(linkedResources):
    if not linkedResources:
        return {"folders": [], "items": []}
    result = {
        "folders": linkedResources.get("folders", []),
        "items": linkedResources.get("items", []),
    }
    if "filter" in linkedResources:
        result["filter"] = linkedResources["filter"]
    return result


def getLinkedResources(item):
    resources = item.get("meta", {}).get("linkedResources", {})
    return normalizeLinkedResources(resources)


def getTouchedTime(item):
    lastOpened = item.get("meta", {}).get("lastOpened")
    if lastOpened:
        if isinstance(lastOpened, datetime):
            return lastOpened
        return datetime.strptime(lastOpened, "%Y-%m-%dT%H:%M:%S.%fZ")
    return item.get("updated") or item.get("created")


def getNewestDoc(docs):
    loadedDocs = [doc for doc in docs if doc]
    if not loadedDocs:
        return None
    return max(loadedDocs, key=getTouchedTime)


def findNewestSession(items):
    return getNewestDoc([item for item in items if isSessionItem(item)])


def getFilteredFiles(folder, filters):
    """
    Given a folder and a set of item filter criteria, find all files that are
    in items in the folder or any of its sub-folders that match the filter.
    Accepts a single filter dict or a list of dicts (OR-unioned).
    """
    filtersList = _promoteFilterToList(filters) or []
    if len(filtersList) > 1:
        itemMatch = {"$or": filtersList}
    elif filtersList:
        itemMatch = filtersList[0]
    else:
        itemMatch = {}
    folderId = folder["_id"]
    pipeline = [
        {"$match": {"_id": folderId}},
        {
            "$graphLookup": {
                "from": "folder",
                "connectFromField": "_id",
                "connectToField": "parentId",
                "depthField": "_depth",
                "as": "folders",
                "startWith": "$_id",
            }
        },
        {"$addFields": {"folders": {"$concatArrays": [[{"_id": "$_id"}], "$folders"]}}},
        {"$unwind": "$folders"},
        {"$replaceRoot": {"newRoot": "$folders"}},
        # Ignore a job's private output folder: its result files stay durable but
        # never surface as ordinary launch data. Output folders hold files
        # directly (no nested subfolders), so excluding the marked folder itself
        # excludes all its outputs.
        {"$match": {"meta.%s" % JOB_OUTPUT_FOLDER_META_KEY: {"$ne": True}}},
        {
            "$lookup": {
                "from": "item",
                "localField": "_id",
                "foreignField": "folderId",
                "as": "items",
            }
        },
        {"$unwind": "$items"},
        {"$replaceRoot": {"newRoot": "$items"}},
        {"$match": itemMatch},
        {
            "$lookup": {
                "from": "file",
                "localField": "_id",
                "foreignField": "itemId",
                "as": "files",
            }
        },
        {"$unwind": "$files"},
        {"$replaceRoot": {"newRoot": "$files"}},
    ]
    logger.debug("Filtering pipeline: %s", pipeline)
    filesInFolder = list(Folder().collection.aggregate(pipeline))
    return filesInFolder


def filterMatchesSession(rowFilter, sessionFilter):
    rowList = _promoteFilterToList(rowFilter)
    sessionList = _promoteFilterToList(sessionFilter)
    if rowList is None or sessionList is None:
        return False

    def canon(filterDict):
        return tuple(sorted(filterDict.items()))

    return sorted(map(canon, rowList)) == sorted(map(canon, sessionList))


def getFilteredSessionFile(folder, filters, user):
    candidates = list(
        Item().find(
            {
                "folderId": folder["_id"],
                "meta.linkedResources.filter": {"$exists": True},
            },
            fields=[
                "_id",
                "name",
                "meta.linkedResources",
                "meta.lastOpened",
                "updated",
                "created",
            ],
        )
    )
    matches = [
        item
        for item in candidates
        if filterMatchesSession(
            filters,
            item.get("meta", {}).get("linkedResources", {}).get("filter"),
        )
    ]
    item = findNewestSession(matches)
    if not item:
        return None
    item = Item().load(item["_id"], user=user, level=AccessType.READ)
    return singleVolViewZipOrImageFiles(
        Item().fileList(item, subpath=False, data=False), user=user
    )
