"""Server-fixture coverage for the ownership-boundary deletion cascade.

Drives the ``model.job.remove`` handler described in ``backend/outputs.py``: a
non-terminal owned job is refused, a terminal one cascades to its output folder
and staged inputs, and a raising cascade aborts the delete so the job is retained
and the delete stays retryable.

Needs a live pytest-girder Mongo; the module self-skips when it is unreachable.
"""

import io
from conftest import mongo_reachable
import uuid

import pytest

from girder_volview.backend import inputs, outputs, routes


pytestmark = pytest.mark.skipif(
    not mongo_reachable(),
    reason="needs a live pytest-girder Mongo (like test_job_addressed_routes); "
    "unavailable offline",
)


DELETE_PATH = "/volview_processing/jobs/%s"


# ---------------------------------------------------------------------------
# Users / launch folder
# ---------------------------------------------------------------------------


@pytest.fixture
def owner(db):
    from girder.models.user import User

    return User().createUser(
        login="deleteowner",
        password="password123",
        firstName="D",
        lastName="O",
        email="deleteowner@example.com",
        admin=False,
    )


@pytest.fixture
def stranger(db):
    from girder.models.user import User

    return User().createUser(
        login="deletestranger",
        password="password123",
        firstName="N",
        lastName="A",
        email="deletestranger@example.com",
        admin=False,
    )


@pytest.fixture
def launchFolder(fsAssetstore, owner):
    from girder.models.folder import Folder

    return Folder().createFolder(
        owner, "launch", parentType="user", creator=owner, public=False
    )


# ---------------------------------------------------------------------------
# Helpers -- a job that OWNS a real private output folder, driven through the
# real girder_jobs state machine
# ---------------------------------------------------------------------------


def _reload(job):
    from girder_jobs.models.job import Job

    return Job().load(job["_id"], force=True)


def _drive(job, status):
    from girder_jobs.constants import JobStatus
    from girder_jobs.models.job import Job

    paths = {
        JobStatus.QUEUED: [JobStatus.QUEUED],
        JobStatus.RUNNING: [JobStatus.QUEUED, JobStatus.RUNNING],
        JobStatus.SUCCESS: [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.SUCCESS],
        JobStatus.ERROR: [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.ERROR],
    }
    for s in paths.get(status, []):
        job = Job().updateJob(_reload(job), status=s)
    return _reload(job)


def _makeOwnedJob(owner, launchFolder, status=None, public=False):
    """A job owning a REAL private output folder (created exactly as runTask does)."""
    from girder_jobs.models.job import Job

    outputFolder = routes._createJobOutputFolder(launchFolder, owner, uuid.uuid4().hex)
    job = Job().createJob(
        title="t",
        type="volview_test",
        user=owner,
        public=public,
        otherFields={
            outputs._OUTPUT_FOLDER_ID_FIELD: str(outputFolder["_id"]),
            inputs._LAUNCH_FOLDER_FIELD: str(launchFolder["_id"]),
            outputs._OUTPUTS_FIELD: {},
        },
    )
    if status is not None:
        job = _drive(job, status)
    return _reload(job), outputFolder


def _stageTransientInput(owner, launchFolder, job):
    """Stage a transient input item and record it on the (already terminal) job.

    Stamped AFTER the job is terminal so the terminal-state transient cleanup did
    not already remove it -- the DELETE cascade is then the unambiguous remover."""
    from girder.models.item import Item
    from girder.models.upload import Upload
    from girder_jobs.models.job import Job

    fileDoc = Upload().uploadFromFile(
        io.BytesIO(b"seg-bytes"),
        size=9,
        name="staged.seg.nrrd",
        parentType="folder",
        parent=launchFolder,
        user=owner,
    )
    itemId = fileDoc["itemId"]
    Item().setMetadata(
        Item().load(itemId, force=True), {inputs._TRANSIENT_META_KEY: True}
    )
    Job().collection.update_one(
        {"_id": job["_id"]},
        {"$set": {inputs._TRANSIENT_META_KEY: [str(itemId)]}},
    )
    return itemId


def _delete(server, jobId, user):
    return server.request(
        path=DELETE_PATH % jobId,
        method="DELETE",
        user=user,
        isJson=False,
        # exception=True permits a 500 (the injected-failure retry case) without
        # the helper asserting; it is harmless for the handled 204/403/409 cases.
        exception=True,
    )


def _folderExists(folderId):
    from girder.models.folder import Folder

    return Folder().load(folderId, force=True, exc=False) is not None


def _jobExists(jobId):
    from girder_jobs.models.job import Job

    return Job().load(jobId, force=True, exc=False) is not None


def _itemExists(itemId):
    from girder.models.item import Item

    return Item().load(itemId, force=True, exc=False) is not None


@pytest.mark.plugin("volview")
def test_nonterminal_delete_409s_and_retains_ownership(server, owner, launchFolder):
    from girder_jobs.constants import JobStatus

    # Both a pending (INACTIVE, status=None) and a running owned job are refused.
    for status in (None, JobStatus.RUNNING):
        job, outputFolder = _makeOwnedJob(owner, launchFolder, status=status)
        resp = _delete(server, job["_id"], owner)
        assert resp.output_status.startswith(b"409")
        assert _jobExists(job["_id"])
        assert _folderExists(outputFolder["_id"])


@pytest.mark.plugin("volview")
def test_terminal_delete_cascades_folder_inputs_and_job(server, owner, launchFolder):
    from girder_jobs.constants import JobStatus

    job, outputFolder = _makeOwnedJob(owner, launchFolder, status=JobStatus.SUCCESS)
    stagedItemId = _stageTransientInput(owner, launchFolder, job)
    assert _folderExists(outputFolder["_id"])
    assert _itemExists(stagedItemId)

    resp = _delete(server, job["_id"], owner)
    assert resp.output_status.startswith(b"204")

    assert not _folderExists(outputFolder["_id"])
    assert not _itemExists(stagedItemId)
    assert not _jobExists(job["_id"])


@pytest.mark.plugin("volview")
def test_partial_deletion_failure_retains_job_then_retry_completes(
    server, owner, launchFolder, monkeypatch
):
    from girder.models.folder import Folder
    from girder_jobs.constants import JobStatus

    job, outputFolder = _makeOwnedJob(owner, launchFolder, status=JobStatus.SUCCESS)

    original_remove = Folder.remove
    calls = {"n": 0}

    def flaky_remove(self, doc, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated owned-folder removal failure")
        return original_remove(self, doc, **kwargs)

    monkeypatch.setattr(Folder, "remove", flaky_remove)

    # The cascade raises, so the model never reaches the DB delete: the job is
    # RETAINED as the discoverable owner and the API errors, never a false 204.
    first = _delete(server, job["_id"], owner)
    assert first.output_status.startswith(b"500")
    assert _jobExists(job["_id"])
    assert _folderExists(outputFolder["_id"])

    second = _delete(server, job["_id"], owner)
    assert second.output_status.startswith(b"204")
    assert not _jobExists(job["_id"])
    assert not _folderExists(outputFolder["_id"])


@pytest.mark.plugin("volview")
def test_read_only_user_cannot_delete(server, owner, stranger, launchFolder):
    from girder_jobs.constants import JobStatus

    # A public terminal job: the stranger has READ (could see it) but not WRITE.
    job, outputFolder = _makeOwnedJob(
        owner, launchFolder, status=JobStatus.SUCCESS, public=True
    )

    resp = _delete(server, job["_id"], stranger)
    assert resp.output_status.startswith(b"403")

    # The WRITE-gated load blocks the read-only viewer before any cascade runs.
    assert _jobExists(job["_id"])
    assert _folderExists(outputFolder["_id"])


@pytest.mark.plugin("volview")
def test_core_job_remove_of_nonterminal_owned_job_is_blocked(
    server, owner, launchFolder
):
    # The model.job.remove guard is not confined to this plugin's DELETE route: a
    # direct JobModel().remove (what Girder's core /job/:id DELETE ultimately
    # calls) against a non-terminal OWNED job is blocked too.
    from girder.exceptions import RestException
    from girder_jobs.constants import JobStatus
    from girder_jobs.models.job import Job

    job, outputFolder = _makeOwnedJob(owner, launchFolder, status=JobStatus.RUNNING)

    with pytest.raises(RestException) as exc:
        Job().remove(_reload(job))
    assert exc.value.code == 409

    assert _jobExists(job["_id"])
    assert _folderExists(outputFolder["_id"])
