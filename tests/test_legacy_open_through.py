"""Legacy ``session.volview.zip`` open-through against real Girder models.

Exercises the ordinary launch composer (``backend/launch.py``) through the
live cherrypy pipeline:

- **bare folder-open** resumes the folder's newest ``session.volview.zip`` as
  the byte-identical legacy ``{resources}`` manifest; with no zip it opens the
  folder's raw loadable images instead;
- **explicit zip open (item route):** a ``session.volview.zip`` item keeps
  opening as its resources list (restore) -- the image half of the same rung;
- **empty gesture** (no zip, no loadable images) fails closed at the route;
- **merely opening writes NOTHING:** GETs of both manifest routes mutate no
  folder/item/file doc (the read paths are read-only).
"""

import io
from conftest import mongo_reachable

import pytest

from girder_volview.utils import filesToManifest, makeFileDownloadUrl


def _uploadFile(folder, user, name, data=b"pixels"):
    """Upload one file into ``folder``; return (item, file)."""
    from girder.models.item import Item
    from girder.models.upload import Upload

    fileDoc = Upload().uploadFromFile(
        io.BytesIO(data),
        size=len(data),
        name=name,
        parentType="folder",
        parent=folder,
        user=user,
    )
    item = Item().load(fileDoc["itemId"], force=True)
    return item, fileDoc


# ---------------------------------------------------------------------------
# Self-skip when no live test Mongo is reachable
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not mongo_reachable(),
    reason="needs a live pytest-girder Mongo; unavailable offline",
)


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def owner(db):
    from girder.models.user import User

    return User().createUser(
        login="legacyowner",
        password="password123",
        firstName="A",
        lastName="B",
        email="legacyowner@example.com",
        admin=False,
    )


@pytest.fixture
def studyFolder(fsAssetstore, owner):
    from girder.models.folder import Folder

    return Folder().createFolder(
        owner, "study", parentType="user", creator=owner, public=False
    )


def _getJson(server, path, user, **kwargs):
    return server.request(path=path, method="GET", user=user, isJson=True, **kwargs)


def _folderManifest(server, folder, user, **kwargs):
    return _getJson(server, "/folder/%s/volview" % folder["_id"], user, **kwargs)


def _itemManifest(server, item, user, **kwargs):
    return _getJson(server, "/item/%s/volview" % item["_id"], user, **kwargs)


def _legacyManifestFor(fileDoc, folder):
    """The byte-identical legacy ``{resources}`` manifest for one zip file."""
    return filesToManifest([(fileDoc["name"], fileDoc)], folder["_id"])


# ---------------------------------------------------------------------------
# 1. Legacy zip open-through selection (bare folder-open)
# ---------------------------------------------------------------------------


@pytest.mark.plugin("volview")
def test_no_native_snapshot_newest_zip_opens_byte_identical(server, owner, studyFolder):
    _, fileA = _uploadFile(studyFolder, owner, "brain.nrrd")
    _, zipOld = _uploadFile(studyFolder, owner, "old.volview.zip", data=b"oldzip")
    _, zipNew = _uploadFile(studyFolder, owner, "new.volview.zip", data=b"newzip")

    resp = _folderManifest(server, studyFolder, owner, exception=True)
    # Byte-identical legacy shape: the newest zip + the config entry, nothing
    # else.
    assert resp.json == _legacyManifestFor(zipNew, studyFolder)


@pytest.mark.plugin("volview")
def test_no_snapshot_no_zip_is_ephemeral_composed(server, owner, studyFolder):
    _, fileA = _uploadFile(studyFolder, owner, "brain.nrrd")

    resp = _folderManifest(server, studyFolder, owner, exception=True)
    # No session zip: the bare folder-open falls to the folder's raw loadable
    # images -- the legacy {resources} shape, the image + config entries only.
    resources = resp.json["resources"]
    names = [resource["name"] for resource in resources]
    assert "brain.nrrd" in names
    assert names[-1] == "config.json"
    assert any(
        resource["name"] == "brain.nrrd"
        and resource["url"] == makeFileDownloadUrl(fileA)
        for resource in resources
    )


@pytest.mark.plugin("volview")
def test_empty_folder_opens_config_only_manifest(server, owner, studyFolder):
    # No zip, no loadable images: the restored compose-direct folder-open returns
    # an empty manifest (only the config.json resource), matching main -- the
    # launcher hides the button for unloadable folders anyway.
    resp = _folderManifest(server, studyFolder, owner, exception=True)
    assert [r["name"] for r in resp.json["resources"]] == ["config.json"]


# ---------------------------------------------------------------------------
# 2. Explicit zip open (item route) -- regression for the resolver 400
# ---------------------------------------------------------------------------


@pytest.mark.plugin("volview")
def test_item_route_session_zip_opens_through(server, owner, studyFolder):
    from girder.models.item import Item

    _, zipFile = _uploadFile(studyFolder, owner, "old.volview.zip", data=b"zip")
    zipItem = Item().load(zipFile["itemId"], force=True)

    # Without the session-zip branch this would 400 at the resolver (session
    # files are not loadable bases): the item route opens the zip through as its
    # legacy resources list (restore).
    resp = _itemManifest(server, zipItem, owner, exception=True)
    assert resp.json == _legacyManifestFor(zipFile, studyFolder)


# ---------------------------------------------------------------------------
# 3. Merely opening writes NOTHING
# ---------------------------------------------------------------------------


@pytest.mark.plugin("volview")
def test_opening_writes_nothing(server, owner, studyFolder):
    from girder.models.folder import Folder
    from girder.models.item import Item

    baseItem, fileA = _uploadFile(studyFolder, owner, "brain.nrrd")
    _, zipFile = _uploadFile(studyFolder, owner, "old.volview.zip", data=b"zip")
    zipItem = Item().load(zipFile["itemId"], force=True)

    before = {
        "folder": Folder().load(studyFolder["_id"], force=True),
        "baseItem": Item().load(baseItem["_id"], force=True),
        "zipItem": Item().load(zipItem["_id"], force=True),
    }

    # All three read shapes: the legacy-zip fallback open (folder route), the
    # explicit zip open (item route), and an ephemeral compose (image item).
    _folderManifest(server, studyFolder, owner, exception=True)
    _itemManifest(server, zipItem, owner, exception=True)
    _itemManifest(server, baseItem, owner, exception=True)

    assert Folder().load(studyFolder["_id"], force=True) == before["folder"]
    assert Item().load(baseItem["_id"], force=True) == before["baseItem"]
    assert Item().load(zipItem["_id"], force=True) == before["zipItem"]
