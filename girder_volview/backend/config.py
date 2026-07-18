"""Processing backend -- the per-launch provider-config block.

The launch manifest injects this block so the client knows where to reach the
processing provider (``baseUrl`` / ``jobsBaseUrl``) for the folder it opened.
Both URLs derive from the runtime ``getApiRoot()`` so a non-default API mount
still resolves.
"""

from girder.utility.server import getApiRoot


def _providerBaseUrl(folder):
    # Origin-relative and keyed off getApiRoot() -- the SAME mount
    # utils.makeFileDownloadUrl and inputs._fileIdFromMintedUri use. Hardcoding
    # "/api/v1" 404s every submit/status/results/stage call on a non-default
    # API mount (e.g. /girder/api/v1).
    return f"/{getApiRoot()}/folder/{folder['_id']}/volview_processing"


# The folder-free root for the job-addressed routes (status/results/cancel),
# keyed by job id alone and mounted on the ``volview_processing`` resource, a
# sibling of ``/folder`` (see routes.py ``_JobResource``). Advertised explicitly
# so the client never string-surgeries the folder segment out of ``baseUrl``. A
# function because it derives from the runtime ``getApiRoot()``.
def _jobsBaseUrl():
    return f"/{getApiRoot()}/volview_processing"


def _providerConfigForFolder(folder):
    # The block advertises only where to reach the provider, never what is
    # loaded: the client mints its own input refs from the on-screen volume's
    # provenance. The client zod schema (`src/processing/config.ts`
    # processingProviderConfig) reads only id/label/baseUrl/jobsBaseUrl/context
    # and strips unknown keys, so an added field stays compatible.
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
