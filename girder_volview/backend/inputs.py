"""Processing backend -- input resolution + transient staging lifecycle.

This module owns the two halves:

- **Resolution**: recovering Girder ids from the provenance handles the client
  round-trips -- each bound *input*'s backend-minted proxiable URIs
  (``resolveInputUrisToFiles``). The backend reads its OWN URL scheme; strict
  own-scheme validation plus a per-user ACL re-check are the boundary.
- **Transient staging**: client-held bytes earn provenance through
  ``stageInput`` (the route lives in ``routes.py``); the two cleanup obligations
  (job-end deletion + orphan sweep) live here alongside the launch-context stamp
  a reloaded client re-discovers its jobs by.
"""

import datetime

from bson.objectid import ObjectId
from girder import logger
from girder.constants import AccessType
from girder.exceptions import AccessException, RestException
from girder.models.file import File
from girder.models.item import Item
from girder.models.upload import Upload

from ..handles import parseFileHandle
from ..utils import TRANSIENT_STAGED_META_KEY

# ---------------------------------------------------------------------------
# Input URIs → file ids (the backend reading its own mint)
#
# The client submits each bound input as ``{type, format?, uris}`` where every
# uri is a backend-minted, origin-relative ``/<apiRoot>/file/<id>/proxiable/<name>``
# (``utils.makeFileDownloadUrl``). Resolution recovers the file id from that exact
# shape and nothing else, then re-checks READ access under the submitting user.
# It is type-agnostic: an image, a labelmap, or any future input all
# resolve through this one path — the backend never branches on ``type``.
# ---------------------------------------------------------------------------


def _fileIdFromMintedUri(uri):
    """Recover the Girder file id from a backend-minted proxiable uri, or ``None``.

    A thin delegate of :func:`girder_volview.handles.parseFileHandle` -- the
    ONE parse site for the load-handle scheme, the exact mirror of
    the mint (``handles.mintFileHandle`` / ``utils.makeFileDownloadUrl``), so
    the parser accepts every handle the backend emits -- including
    percent-encoded reserved characters in the file name and pre-fix raw-name
    legacy mints. Returns ``None`` for anything outside the backend's own
    scheme, so a foreign or malformed string is rejected by the caller and
    never dereferenced.
    """
    parsed = parseFileHandle(uri)
    return parsed[0] if parsed else None


def resolveInputUrisToFiles(uris, user):
    """Resolve a client-minted uri list to readable Girder files (fail closed).

    Two obligations: (1) **strict own-scheme validation** — a
    uri that does not match the backend's mint is rejected 400 and never fetched;
    and (2) an **ACL re-check** — every recovered id is loaded with the submitting
    user's READ permission, so possession of a (by-design recoverable) id is not
    itself a capability. Validation runs over every uri first, then authorization,
    so a malformed uri fails 400 ahead of an unreadable id's 403.
    """
    if not isinstance(uris, list) or not uris:
        raise RestException("Processing input value carries no uris", code=400)
    fileIds = []
    for uri in uris:
        fileId = _fileIdFromMintedUri(uri)
        if fileId is None:
            raise RestException(
                "Processing input uri does not match this server's file scheme",
                code=400,
            )
        fileIds.append(fileId)
    return _readableFilesInOrder(fileIds, user)


def _readableFilesInOrder(fileIds, user):
    """Load READ-authorized file docs for ``fileIds``, batched, in input order.

    Girder files inherit access through their parent item, and ``File().load``
    with a user+level runs a per-file ACL that falls back to loading that parent
    -- so a per-uri load is ~2 Mongo queries EACH (≈600 for a 300-slice DICOM
    series). This mirrors ``results._readableOutputFilesForJobs``: one
    ``File().find`` for every id, then ONE permission-filtered
    ``Item().findWithPermissions`` over the distinct parent items. The ACL
    boundary is identical -- a file whose parent item the user cannot READ (or a
    missing file/parent) raises the same ``AccessException`` the per-file load
    raised. ``_fileIdFromMintedUri`` already validated each id's shape, so the
    ``ObjectId`` conversion cannot fail here.

    Ordering matches the input ``fileIds`` (a comma-joined multi-file volume
    forwards ids positionally), and a repeated id resolves to the same doc.
    """
    objectIds = [ObjectId(fileId) for fileId in fileIds]
    filesById = {
        str(fileDoc["_id"]): fileDoc
        for fileDoc in File().find(query={"_id": {"$in": objectIds}})
    }
    itemIds = {fileDoc.get("itemId") for fileDoc in filesById.values()}
    itemIds.discard(None)
    readableItemIds = (
        {
            itemDoc["_id"]
            for itemDoc in Item().findWithPermissions(
                query={"_id": {"$in": list(itemIds)}},
                user=user,
                level=AccessType.READ,
            )
        }
        if itemIds
        else set()
    )
    files = []
    for fileId in fileIds:
        fileDoc = filesById.get(fileId)
        if fileDoc is None or fileDoc.get("itemId") not in readableItemIds:
            # Missing file, missing parent, or a parent the user cannot READ:
            # possession of a (by-design recoverable) id is not a capability.
            raise AccessException("Read access denied for file %s." % fileId)
        files.append(fileDoc)
    return files


def validateStagedReferenceImage(referenceImage, user):
    """Validate a staged labelmap's reference image (own-scheme + ACL + durable).

    Resolves the reference image's own-scheme uris to files under the caller's
    READ permission — rejecting a malformed, foreign, or unauthorized reference
    — and rejects a transient reference so a staged labelmap
    never binds to ephemeral data. Validation only: no lineage is tracked.
    """
    if not isinstance(referenceImage, dict) or referenceImage.get("type") != "image":
        raise RestException("Staged labelmap requires a reference image", code=400)
    fileDocs = resolveInputUrisToFiles(referenceImage.get("uris"), user)
    itemIds = {fileDoc.get("itemId") for fileDoc in fileDocs}
    itemIds.discard(None)
    # ``resolveInputUrisToFiles`` already enforced READ on each file's parent item,
    # so this read only inspects the transient marker: one batched find over the
    # DISTINCT parent items replaces a per-file (and per-duplicate) ``Item().load``.
    for item in Item().find(query={"_id": {"$in": list(itemIds)}}):
        if _isTransientItem(item):
            raise RestException(
                "Staged labelmap requires a durable reference image", code=400
            )


# ---------------------------------------------------------------------------
# Transient staging lifecycle
#
# Client-held bytes (a painted labelmap, and any future
# consented upload) earn provenance through the type-agnostic staging endpoint
# (``stageInput`` in ``routes.py``): the bytes land in a fresh item tagged
# transient and the backend mints its own proxiable download URI for them. From
# that point a staged input is indistinguishable from any other minted input and
# resolves through the exact same own-scheme path (``resolveInputUrisToFiles``)
# — the backend never branches on ``type``.
#
# Ownership is per-job BY CONSTRUCTION: at submit, every transient staged
# input is COPIED into the job's private folder and the CLI params are
# rewritten onto the copies (``copyStagedInputsIntoJobFolder``). No job ever
# references a shared staged original, so there is no claim bookkeeping and no
# submit-versus-cleanup race between jobs reusing one staged input. Two
# cleanup obligations remain:
#   1. Job-side: the job's OWN copies are recorded on it;
#      ``_cleanupTransientOnJobDone`` (bound to ``jobs.job.update.after``)
#      deletes them once the job reaches a terminal state (the job-deletion
#      cascade removes the private folder as a backstop).
#   2. Orphan sweep: a staged ORIGINAL never has a job to clean it up, so each
#      staging call ages out transient items older than the TTL, keyed off
#      ``item['created']`` (the marker carries no timestamp).
# ---------------------------------------------------------------------------

# The staged-input marker's canonical definition lives in ``utils`` next to its
# sibling ``JOB_OUTPUT_FOLDER_META_KEY`` (so ``utils.isTransientStagedFile`` reads
# it without reaching up into this package). This module-local name is the alias
# the rest of the backend + tests reference as ``inputs._TRANSIENT_META_KEY``.
_TRANSIENT_META_KEY = TRANSIENT_STAGED_META_KEY

# Age after which an uploaded-but-never-submitted transient item is swept on the
# next staging call. Upload->submit is normally seconds; a day absorbs an
# interrupted session without cluttering folders across days.
_TRANSIENT_ORPHAN_TTL = datetime.timedelta(hours=24)


def _isTransientItem(item):
    """Whether an item carries the staging marker."""
    return bool((item or {}).get("meta", {}).get(_TRANSIENT_META_KEY))


def copyStagedInputsIntoJobFolder(params, resolvedInputFiles, user, outputFolder):
    """Give the job its OWN copies of any staged (transient) inputs.

    Every transient staged item among a submission's bound inputs is copied
    into the job's private folder and the CLI file-id params are rewritten
    onto the copies, so the job references only resources it alone owns. Two
    jobs reusing the same staged original therefore can never delete each
    other's inputs — there is no shared claim to track and no
    submit-versus-cleanup race. The original stays covered by the plain TTL
    orphan sweep. Type-agnostic: transience is decided by the parent item's
    marker, never by ``type``.

    The parent items were already loaded under the submitting user's READ
    permission during URI resolution; the copy re-loads under that same
    permission. A parent that vanished in between (a concurrent orphan sweep
    or delete) raises 409 — the submit fails and rolls back rather than
    publishing a job against deleted file ids. ``Item().copyItem``
    deep-copies metadata, so a copy carries the transient marker and is
    cleaned up exactly like any staged item.

    Returns ``(params, copiedItemIds)`` — the (possibly rewritten) params and
    the copied item ids to record on the job for terminal cleanup.
    """
    fileIdRemap = {}
    copiedItemIds = []
    mappingByItemId = {}
    for fileDocs in resolvedInputFiles.values():
        for fileDoc in fileDocs:
            itemId = (fileDoc or {}).get("itemId")
            if not itemId:
                continue
            key = str(itemId)
            if key not in mappingByItemId:
                item = Item().load(itemId, user=user, level=AccessType.READ, exc=False)
                if item is None:
                    # URI resolution ACL-loaded this parent moments ago, so a
                    # missing item means a concurrent delete (e.g. the orphan
                    # TTL sweep of another staging call) won the race. Fail the
                    # submit rather than publish a job whose params reference
                    # deleted files.
                    raise RestException(
                        "A processing input was removed while the submission "
                        "was in progress; please resubmit",
                        code=409,
                    )
                if not _isTransientItem(item):
                    mappingByItemId[key] = None
                else:
                    copied = Item().copyItem(item, creator=user, folder=outputFolder)
                    copiedItemIds.append(str(copied["_id"]))
                    # Copied files preserve their names; sorting both sides by
                    # name pairs each original file with its copy regardless of
                    # the underlying cursor order.
                    originals = sorted(
                        Item().childFiles(item), key=lambda f: f.get("name", "")
                    )
                    copies = sorted(
                        Item().childFiles(copied), key=lambda f: f.get("name", "")
                    )
                    # strict: copyItem duplicates every child file, so a length
                    # mismatch means a broken copy — fail the submit loudly
                    # rather than run the job against a partial input.
                    mappingByItemId[key] = {
                        str(orig["_id"]): str(cop["_id"])
                        for orig, cop in zip(originals, copies, strict=True)
                    }
            mapping = mappingByItemId[key]
            if mapping:
                fileId = str(fileDoc["_id"])
                if fileId in mapping:
                    fileIdRemap[fileId] = mapping[fileId]
    if not fileIdRemap:
        return params, copiedItemIds
    params = dict(params)
    for paramName, fileDocs in resolvedInputFiles.items():
        params[paramName] = ",".join(
            fileIdRemap.get(str(fileDoc["_id"]), str(fileDoc["_id"]))
            for fileDoc in fileDocs
        )
    return params, copiedItemIds


# ---------------------------------------------------------------------------
# Launch-context stamp. Girder jobs are user-owned, not folder-linked,
# and the output-reference binding adds no job->folder link — so
# `listJobHistory` can only scope to this launch folder's jobs if
# the launch context is recorded ON the job at submit. Plain otherFields
# (queryable Mongo keys), not a schema change. The task id remains backend
# association data; the lightweight history summary deliberately does not
# expose it. The list query reads `_LAUNCH_FOLDER_FIELD`.
# ---------------------------------------------------------------------------
_LAUNCH_FOLDER_FIELD = "volviewLaunchFolderId"  # str(folder _id) — scope key
_TASK_ID_FIELD = "volviewTaskId"


def _removeTransientItems(itemIds):
    """Delete transient input items by id (idempotent, best-effort)."""
    for itemId in itemIds:
        try:
            item = Item().load(itemId, force=True)
            if item:
                Item().remove(item)
        except Exception:
            logger.exception("Failed to remove transient item %s", itemId)


def _cleanupTransientOnJobDone(event):
    """Delete a job's transient staged inputs once it reaches a terminal state.

    Bound to ``jobs.job.update.after``. Idempotent: a re-fired terminal update
    finds the items already gone and no-ops. A present, non-terminal in-memory
    status short-circuits before any DB work (see below), so the common
    progress/log tick costs nothing. When the in-memory status is terminal or
    absent, the job is reloaded from the database before reading the
    marker/status -- ``updateJob`` fires this event with the updater's *in-memory*
    job dict, which carries the marker only if that updater happened to DB-load
    the job first. Reloading keeps cleanup self-contained: it works for any
    terminal updater (girder_worker, a manual cancel, ...), not just this
    backend's own ``updateJob`` call.
    """
    from girder_jobs.models.job import Job as JobModel

    from .results import isTerminalStatus

    info = getattr(event, "info", None)
    eventJob = info.get("job") if isinstance(info, dict) else None
    if not isinstance(eventJob, dict):
        return
    # This handler fires on EVERY job update instance-wide (progress ticks, log
    # appends), the vast majority of which are non-terminal and can never trigger
    # cleanup. ``updateJob`` sets the new status ON the in-memory job dict before
    # firing this event, so a present, non-terminal in-memory status is an
    # authoritative "not settled yet" -- short-circuit before the DB reload the
    # steady-state stream would otherwise pay on every tick. Only a terminal or
    # absent in-memory status falls through to the reload-before-acting path.
    inMemoryStatus = eventJob.get("status")
    if inMemoryStatus is not None and not isTerminalStatus(inMemoryStatus):
        return
    # Reload the committed doc to read the marker/status self-containedly -- the
    # event's in-memory job dict may carry neither. includeLog=False: this handler
    # is bound to ``jobs.job.update.after`` and fires on EVERY update, but only ever
    # reads the transient marker + status, never the log. Loading it with the
    # (unbounded) log would re-materialize the whole log out of Mongo on every
    # progress/log tick -- pure cost that grows with the job's chattiness.
    job = JobModel().load(eventJob.get("_id"), force=True, includeLog=False)
    if not isinstance(job, dict):
        return
    transientItemIds = job.get(_TRANSIENT_META_KEY)
    if not isinstance(transientItemIds, list) or not transientItemIds:
        return
    if not isTerminalStatus(job.get("status")):
        return
    _removeTransientItems(transientItemIds)


def _sweepOrphanTransients(folder, now=None):
    """Age out stale transient items in ``folder`` (best-effort).

    Piggybacked on staging calls (an upload precedes its job, so job-end cleanup
    never sees a never-submitted orphan). Keyed off ``item['created']`` because the
    marker carries no timestamp; only items strictly older than
    :data:`_TRANSIENT_ORPHAN_TTL` are candidates, so the item this same call is
    about to create is never one. Age alone decides: no job ever depends on a
    staged ORIGINAL — submission rewires the job onto its own private copies
    (:func:`copyStagedInputsIntoJobFolder`), and those copies live in the job's
    private folder, not the staging folder this sweep scans.
    """
    now = now or datetime.datetime.utcnow()
    cutoff = now - _TRANSIENT_ORPHAN_TTL
    query = {
        "folderId": folder["_id"],
        "meta.%s" % _TRANSIENT_META_KEY: True,
        "created": {"$lt": cutoff},
    }
    try:
        stale = list(Item().find(query))
    except Exception:
        logger.exception("Failed to query orphan transient items")
        return
    for item in stale:
        try:
            Item().remove(item)
        except Exception:
            logger.exception(
                "Failed to sweep orphan transient item %s", item.get("_id")
            )


def _streamMultipartFileIntoItem(folder, user, part, name):
    """Stream one parsed multipart file part into a fresh item under ``folder``.

    ``folder`` is the WRITE-authorized document the ``stageInput`` route already
    loaded via its ``modelParam(level=AccessType.WRITE)`` decorator — the single
    authorization boundary. This helper does not re-load/re-check it (a second
    WRITE load would only duplicate the gate and blur where authorization lives).
    """
    stream = getattr(part, "file", None)
    if stream is None:
        raise RestException("Staging request carries no file part", code=400)
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    if size <= 0:
        raise RestException("Staging file must not be empty", code=400)
    return Upload().uploadFromFile(
        stream,
        size=size,
        name=name,
        parentType="folder",
        parent=folder,
        user=user,
        mimeType="application/octet-stream",
    )


def _tagItemTransient(fileDoc, user):
    """Tag a freshly-uploaded file's parent item transient; return the item."""
    itemId = fileDoc.get("itemId")
    if not itemId:
        return None
    item = Item().load(itemId, force=True)
    if item:
        Item().setMetadata(item, {_TRANSIENT_META_KEY: True})
    return item
