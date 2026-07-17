"""Processing backend -- the per-launch provider-config block.

The launch manifest injects this block so the client knows where to reach the
processing provider (``baseUrl`` / ``jobsBaseUrl``) for the folder it opened.
Both URLs derive from the runtime ``getApiRoot()`` so a non-default API mount
still resolves.
"""

from girder.utility.server import getApiRoot


def _providerBaseUrl(folder):
    # Origin-relative, keyed off getApiRoot() -- the SAME mount
    # utils.makeFileDownloadUrl and inputs._fileIdFromMintedUri use -- so a
    # non-default API mount (e.g. /girder/api/v1) still resolves. A hardcoded
    # "/api/v1" here made every submit/status/results/stage call 404 on such a
    # deployment while file downloads (which use getApiRoot) worked.
    return f"/{getApiRoot()}/folder/{folder['_id']}/volview_processing"


# The folder-free root for the job-addressed routes (status/results/cancel),
# which are keyed by job id alone and mounted on the ``volview_processing``
# resource -- a sibling of ``/folder`` (see routes.py ``_JobResource``). Advertised
# explicitly so the client never string-surgeries the folder segment out of
# ``baseUrl``. A function, not a module constant,
# because -- like ``baseUrl`` -- it derives from the runtime ``getApiRoot()``.
def _jobsBaseUrl():
    return f"/{getApiRoot()}/volview_processing"


def _providerConfigForFolder(folder):
    # No advertised sources: the client mints its own input refs from the
    # on-screen volume's provenance (grouping moved to the client), so the
    # backend advertises only where to reach the provider, not what is loaded.
    #
    # The client zod schema (`src/processing/config.ts` processingProviderConfig)
    # reads only id/label/baseUrl/jobsBaseUrl/context. The former `protocol`/`auth`
    # keys were vestigial wire fields the client never read and were removed — a
    # `protocol` field is "a standing invitation to switch on it". Absent shapes
    # stay compatible (zod strips unknown keys).
    #
    # The provider ID is FOLDER-SCOPED and immutable: it carries the launch
    # folder id so two folders open simultaneously register as two distinct
    # providers (the client keys every job by (providerId, jobId)); a bare
    # "girder-slicer-cli" would make both folders share one mutable identity. The
    # label carries the folder name so the picker distinguishes them (fall back to
    # bare "Analysis" when a folder document has no name).
    folderName = folder.get("name")
    return {
        "id": "girder-slicer-cli:%s" % folder["_id"],
        "label": "Analysis — %s" % folderName if folderName else "Analysis",
        "baseUrl": _providerBaseUrl(folder),
        "jobsBaseUrl": _jobsBaseUrl(),
        "context": {},
    }


def buildProcessingConfigBlock(folder):
    return {"providers": [_providerConfigForFolder(folder)]}
