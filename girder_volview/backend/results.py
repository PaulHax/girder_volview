"""Processing backend -- job status projection + result collection (the read path).

Girder ``JobStatus`` projects to the contract's neutral shapes (never the girder
enum on the wire). Results come from the file ids recorded ON the job by
``outputs._recordJobOutput`` -- reference-bound, never a folder-name scan.
"""

from bson.objectid import ObjectId
from girder import logger
from girder.constants import AccessType
from girder.models.file import File
from girder.models.item import Item

from ..utils import makeFileDownloadUrl, _toIso
from .inputs import _TASK_ID_FIELD
from .outputs import (
    _OUTPUTS_FIELD,
    _OUTPUT_SPECS_FIELD,
    _declaredOutputIdentifiers,
)


# ---------------------------------------------------------------------------
# Status projection — the neutral JobStatus → state map (never the girder enum)
# ---------------------------------------------------------------------------


# Built on first use and cached module-level: both depend on late imports
# (girder_jobs / girder_worker) unavailable at module import time.
_WORKER_ACTIVE_STATES = None
_STATE_MAP = None


def _computeWorkerActiveStates():
    try:
        from girder_worker.utils import CustomJobStatus

        return {
            CustomJobStatus.FETCHING_INPUT,
            CustomJobStatus.CONVERTING_INPUT,
            CustomJobStatus.CONVERTING_OUTPUT,
            CustomJobStatus.PUSHING_OUTPUT,
            CustomJobStatus.CANCELING,
        }
    except Exception:
        return {
            820,  # CustomJobStatus.FETCHING_INPUT
            821,  # CustomJobStatus.CONVERTING_INPUT
            822,  # CustomJobStatus.CONVERTING_OUTPUT
            823,  # CustomJobStatus.PUSHING_OUTPUT
            824,  # CustomJobStatus.CANCELING
        }


def _workerActiveStates():
    """girder_worker ``CustomJobStatus`` active-state codes (with numeric fallback).

    Core's ``JobStatus`` map has no entry for these, so without them a running job
    would default to ``"pending"`` and a polling client would see it REGRESS. The
    numeric literals are the stable wire integers used when girder_worker (an
    optional runtime dependency) is not importable.
    """
    global _WORKER_ACTIVE_STATES
    if _WORKER_ACTIVE_STATES is None:
        _WORKER_ACTIVE_STATES = _computeWorkerActiveStates()
    return _WORKER_ACTIVE_STATES


def _jobStateMap():
    """The girder ``JobStatus`` -> neutral projected-state map, built once.

    girder_jobs constants are not importable until the plugin's dependency is
    loaded, so the map is built lazily and cached.
    """
    global _STATE_MAP
    if _STATE_MAP is None:
        from girder_jobs.constants import JobStatus

        _STATE_MAP = {
            JobStatus.INACTIVE: "pending",
            JobStatus.QUEUED: "pending",
            JobStatus.RUNNING: "running",
            JobStatus.SUCCESS: "success",
            JobStatus.ERROR: "error",
            JobStatus.CANCELED: "cancelled",
        }
    return _STATE_MAP


_TERMINAL_STATUSES = None


def isTerminalStatus(status):
    """Whether a Girder ``JobStatus`` is terminal (SUCCESS / ERROR / CANCELED).

    The single definition of "the job has settled": the terminal-time scan, the
    ownership deletion guard, the transient-input cleanup, and the DELETE route
    all read it, so what counts as terminal cannot drift between them.
    """
    return status in terminalStatuses()


def terminalStatuses():
    """The terminal ``JobStatus`` set itself (for Mongo ``$nin`` queries)."""
    global _TERMINAL_STATUSES
    if _TERMINAL_STATUSES is None:
        from girder_jobs.constants import JobStatus

        _TERMINAL_STATUSES = frozenset(
            {JobStatus.SUCCESS, JobStatus.ERROR, JobStatus.CANCELED}
        )
    return _TERMINAL_STATUSES


def _projectJobState(job):
    """The neutral projected job state (a ``jobStateSchema`` value) from Girder's
    ``JobStatus``.

    The single shared JobStatus->state map, read by BOTH the status projection
    (``_projectJobStatus``) and the handle projection
    (``_projectJobHistorySummary``), so both derive state from one source of
    truth. Neutral names only — never the girder ``JobStatus`` enum on the wire.
    An unknown status maps to ``"pending"`` (fail closed), except girder_worker's
    active states, which project to ``"running"`` so an active job never regresses.
    Output publication never changes this execution state; ``_projectJobFacts``
    carries result readiness separately.
    """
    status = job.get("status")
    state = _jobStateMap().get(status)
    if state is not None:
        return state
    if status in _workerActiveStates():
        return "running"
    return "pending"


def _progressRatio(job):
    """The job's clamped ``[0, 1]`` progress ratio, or ``None`` when unavailable.

    Shared by the status projection and the history summary so the summary never
    rebuilds the full status projection (errorTail log join included) just to read
    progress. The clamp is load-bearing: a worker/CLI reporting >100% (or a
    negative) fails the client's ``min(0).max(1)`` history-page schema and makes it
    reject the WHOLE page, losing re-discovery for every job in it.
    """
    progress = job.get("progress") or {}
    if not progress.get("total") or progress.get("current") is None:
        return None
    try:
        ratio = float(progress["current"]) / float(progress["total"])
    except (TypeError, ZeroDivisionError):
        return None
    return min(1.0, max(0.0, ratio))


def _projectJobStatus(job, user=None):
    """Convert Girder Job status to ProcessingJobStatus."""
    facts = _projectJobFacts(job, user)
    state = facts["state"]
    out = {
        "jobId": str(job["_id"]),
        "state": state,
        "resultState": facts["resultState"],
    }
    if state == "error":
        log = job.get("log") or []
        if isinstance(log, list):
            tail = "".join(log[-20:])
        else:
            tail = str(log)[-2000:]
        out["errorTail"] = tail
    progress = _progressRatio(job)
    if progress is not None:
        out["progress"] = progress
    return out


def _transitionTime(job, status):
    for transition in job.get("timestamps") or []:
        if isinstance(transition, dict) and transition.get("status") == status:
            return transition.get("time")
    return None


def _terminalTime(job):
    """The job's most recent terminal transition time, or ``None``."""
    for transition in reversed(job.get("timestamps") or []):
        if isinstance(transition, dict) and isTerminalStatus(transition.get("status")):
            return transition.get("time")
    return None


def _outputSummary(job, user, facts=None):
    """Neutral output-health counts for the job-history wire shape.

    - ``recorded``: declared outputs that recorded AND resolve to a readable file.
    - ``missing``: settled declared outputs that never recorded, plus recorded
      ids whose file is gone/unreadable.
    """
    facts = facts or _projectJobFacts(job, user)
    return {
        "recorded": len(facts["resolved"]),
        "missing": facts["missing"],
    }


def _projectJobHistorySummary(job, user, readableOutputFiles=None):
    """Project one Girder job into the lightweight history wire shape."""
    from girder_jobs.constants import JobStatus

    creatorName = (
        " ".join(
            filter(
                None,
                [
                    user.get("firstName"),
                    user.get("lastName"),
                ],
            )
        )
        or user.get("login")
        or str(user.get("_id") or "")
    )
    facts = _projectJobFacts(job, user, readableOutputFiles=readableOutputFiles)
    summary = {
        "jobId": str(job["_id"]),
        "taskId": str(job.get(_TASK_ID_FIELD) or ""),
        "taskTitle": str(job.get("title") or job.get(_TASK_ID_FIELD) or ""),
        "createdBy": {"id": str(user["_id"]), "name": creatorName},
        "createdAt": _toIso(job.get("created")) or "",
        "state": facts["state"],
        "resultState": facts["resultState"],
        "outputSummary": _outputSummary(job, user, facts),
    }
    startedAt = _toIso(_transitionTime(job, JobStatus.RUNNING))
    finishedAt = _toIso(_terminalTime(job))
    if startedAt:
        summary["startedAt"] = startedAt
    if finishedAt:
        summary["finishedAt"] = finishedAt
    progress = _progressRatio(job)
    if progress is not None:
        summary["progress"] = progress
    return summary


# ---------------------------------------------------------------------------
# Result intents + collection — reference-bound reads
# ---------------------------------------------------------------------------


def _intentForOutput(out, url, name, jobId):
    """Build the declarative result intent for one output.

    Results cross the wire as declarative intents the client's single applier
    applies — never a ``role`` the client switches on. The vocabulary the client
    validates (VolView ``backend-contract/processing/wire.ts``): a labelmap →
    ``add-segment-group``, a plain image → ``add-base-image``. Any other file
    remains an ordinary result record with no state directive.

    A labelmap intent carries a ``source: {jobId, outputId}`` provenance tag
    (``outputId`` = the CLI's output identifier) so the created segment group
    round-trips the ``.volview.zip`` — display provenance only. A labelmap's
    segment names/colors travel *inside* the ``.seg.nrrd`` file as embedded
    metadata and are read client-side, so the backend sets no ``segments``
    payload; the wire field stays optional. Validates against the contract
    ``result-intent`` schema.
    """
    fileRef = {"url": url, "name": name}
    if out["isLabel"]:
        return {
            "intent": "add-segment-group",
            **fileRef,
            "source": {"jobId": str(jobId), "outputId": out["name"]},
        }
    if out["tag"] == "image":
        return {"intent": "add-base-image", **fileRef}
    return fileRef


def _recordedJobOutputs(job):
    """The ``{identifier: fileId}`` map the upload-finalization handler
    recorded (or {})."""
    outputs = (job or {}).get(_OUTPUTS_FIELD)
    return dict(outputs) if isinstance(outputs, dict) else {}


def _recordedOutputSpecs(job):
    """The declared output specs recorded at submit (or [])."""
    specs = (job or {}).get(_OUTPUT_SPECS_FIELD)
    return list(specs) if isinstance(specs, list) else []


def _projectJobFacts(job, user, readableOutputFiles=None):
    """Project the canonical execution and output-readiness facts for one job.

    Status, history, and result reads all consume this projection so execution
    state never changes to hide output publication, and missing-output accounting
    cannot disagree between endpoints. File readability comes from the ONE
    batched loader (``_readableOutputFilesForJobs``): the history page passes its
    page-wide map in, single-job callers omit it and the loader runs for just this
    job — either way the same two-query ACL check decides readability.
    """
    state = _projectJobState(job)
    if state in {"pending", "running"}:
        return {
            "state": state,
            "resultState": "waiting",
            "resolved": [],
            "missing": 0,
        }
    if state in {"error", "cancelled"}:
        return {
            "state": state,
            "resultState": "unavailable",
            "resolved": [],
            "missing": 0,
        }

    specs = {
        spec["name"]: spec
        for spec in _recordedOutputSpecs(job)
        if isinstance(spec, dict) and isinstance(spec.get("name"), str)
    }
    recorded = _recordedJobOutputs(job)
    if readableOutputFiles is None:
        readableOutputFiles = _readableOutputFilesForJobs([job], user)
    resolved = []
    unreadable = 0
    for outputId, spec in specs.items():
        fileId = recorded.get(outputId)
        if fileId is None:
            continue
        fileDoc = readableOutputFiles.get(str(fileId))
        if fileDoc is None:
            unreadable += 1
        else:
            resolved.append({"out": spec, "fileDoc": fileDoc})

    unrecorded = len(set(specs) - set(recorded))
    missing = unrecorded + unreadable
    return {
        "state": state,
        "resultState": "incomplete" if missing else "ready",
        "resolved": resolved,
        "missing": missing,
    }


def _readableOutputFilesForJobs(jobs, user):
    """Load all readable declared output files for a set of jobs in two queries.

    The ONE loading path deciding output readability — the history page passes
    its whole page, and ``_projectJobFacts`` routes single-job status/result
    reads through it too, so the ACL semantics cannot drift between endpoints.
    The recorded/missing counts are readability-aware, so the ACL check is
    load-bearing. Girder files inherit access through items, and its generic file
    permission query falls back to per-item loads; two bounded queries are used
    instead: fetch the referenced files, then permission-filter their distinct
    parent items. Files with invalid ids, missing parents, or unreadable parents
    are absent from the map and therefore count as missing. The returned map is
    keyed by string id because persisted output ids and model documents may use
    different ObjectId/string representations. The projection carries
    ``name``/``mimeType``/``size`` because ``_collectJobResults`` builds result
    intents (download url + file metadata) from these same docs.
    """
    fileIdsByString = {}
    for job in jobs:
        if _projectJobState(job) != "success":
            continue
        declared = _declaredOutputIdentifiers(job)
        for outputId, fileId in _recordedJobOutputs(job).items():
            if outputId not in declared or fileId is None:
                continue
            try:
                objectId = fileId if isinstance(fileId, ObjectId) else ObjectId(fileId)
            except (TypeError, ValueError):
                continue
            fileIdsByString.setdefault(str(objectId), objectId)
    if not fileIdsByString:
        return {}
    fileDocs = list(
        File().find(
            query={"_id": {"$in": list(fileIdsByString.values())}},
            fields={"_id": 1, "itemId": 1, "name": 1, "mimeType": 1, "size": 1},
        )
    )
    itemIds = {fileDoc.get("itemId") for fileDoc in fileDocs}
    itemIds.discard(None)
    if not itemIds:
        return {}
    readableItemIds = {
        itemDoc["_id"]
        for itemDoc in Item().findWithPermissions(
            query={"_id": {"$in": list(itemIds)}},
            fields={"_id": 1},
            user=user,
            level=AccessType.READ,
        )
    }
    return {
        str(fileDoc["_id"]): fileDoc
        for fileDoc in fileDocs
        if fileDoc.get("itemId") in readableItemIds
    }


def _collectJobResults(job, user, facts=None):
    """Resolve a job's outputs to declarative result intents — reference-bound.

    Reads the ``{identifier: fileId}`` map ``outputs._recordJobOutput`` recorded ON
    the job (never a folder-name scan, never ``_original_params``), resolves the
    files through the batched READ-permission loader, and projects each into its
    result intent with a ``makeFileDownloadUrl`` download url — origin-relative and
    filename-encoded, so non-default API mounts work. Returns ``(results,
    missing)`` where ``missing`` counts settled unrecorded outputs plus recorded
    outputs whose file is gone/unreadable — loss is countable, never a silently
    shorter list. Two concurrent same-name jobs can never cross results: each reads
    only the ids bound to itself.
    """
    facts = facts or _projectJobFacts(job, user)
    resolved = facts["resolved"]

    # The wire shape is the intent object itself — `{intent, url, name, source?}`
    # — plus the `id`/`mimeType`/`size` file metadata the client's JobList reads.
    results = []
    for entry in resolved:
        out = entry["out"]
        fileDoc = entry["fileDoc"]
        url = makeFileDownloadUrl(fileDoc)
        intent = _intentForOutput(out, url, fileDoc["name"], job["_id"])
        result = {
            **intent,
            "id": str(fileDoc["_id"]),
            "mimeType": fileDoc.get("mimeType"),
            "size": fileDoc.get("size"),
        }
        results.append(result)
    return results, facts["missing"]


def _jobResultsPayload(job, user):
    """Apply honest result-read semantics and return the wire result envelope.

    Waiting and unavailable results return the typed conflict body the
    route serves as HTTP 409. Ready and incomplete reads return HTTP 200; partial
    and total output loss both use an incomplete envelope with an accurate count.
    """
    facts = _projectJobFacts(job, user)
    state = facts["state"]
    resultState = facts["resultState"]
    if resultState == "unavailable":
        return {
            "code": "results_unavailable",
            "message": "Job %s results are unavailable (state=%s)"
            % (job.get("_id"), state),
            "state": state,
            "resultState": resultState,
        }
    if resultState == "waiting":
        return {
            "code": "results_not_ready",
            "message": "Job %s results are not ready (resultState=%s)"
            % (job.get("_id"), resultState),
            "state": state,
            "resultState": resultState,
        }
    results, missing = _collectJobResults(job, user, facts)
    if missing:
        logger.info(
            "[volview_processing] job %s: %d declared output(s) missing",
            job.get("_id"),
            missing,
        )
    return {"resultState": resultState, "intents": results, "missing": missing}
