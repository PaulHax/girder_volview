"""Server-fixture coverage for the ``tasks/{id}/spec`` route.

The route translates a task's Slicer XML into VolView's own task spec
server-side (the result-intents contract) and returns it as JSON. Its scope
guards make an
out-of-scope (pathology) / unknown / slicer_cli_web-missing taskId 404
identically -- the server is the boundary.

Like ``test_load`` this needs a live pytest-girder server + Mongo; the module
self-skips when the test Mongo is unreachable so the offline gate stays green
while the wire contract still runs where a Mongo is present.
"""

from conftest import mongo_reachable
import types
from pathlib import Path

import pytest

from girder_volview.backend import slicer_spec, submit

# ---------------------------------------------------------------------------
# Self-skip when no live test Mongo is reachable (mirrors test_load).
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.skipif(
    not mongo_reachable(),
    reason="needs a live pytest-girder Mongo (like test_task_xml_route)",
)


# ---------------------------------------------------------------------------
# Fakes -- a radiology CLI carrying the real MedianFilter XML + a pathology CLI.
# ---------------------------------------------------------------------------

_MEDIAN_XML = (
    Path(__file__).resolve().parent / "slicer_xml" / "median-filter.xml"
).read_text()


def _xml(category, name):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<executable>\n"
        f"  <category>{category}</category>\n"
        f"  <title>{name}</title>\n"
        "  <description>x</description>\n"
        "</executable>\n"
    )


@pytest.fixture
def stub_slicer(monkeypatch):
    """Stub the slicer_cli_web boundary; the real scope filter + handler run."""
    catalog = {
        "radid": types.SimpleNamespace(name="MedianFilter", xml=_MEDIAN_XML),
        "pathid": types.SimpleNamespace(
            name="NucleiDetection", xml=_xml("HistomicsTK", "NucleiDetection")
        ),
    }
    monkeypatch.delenv(submit._ALLOWED_CATEGORIES_ENV, raising=False)
    monkeypatch.setattr(submit, "_slicerCliAvailable", lambda: True)
    monkeypatch.setattr(
        submit, "_findCliItem", lambda taskId, user: catalog.get(taskId)
    )
    return catalog


@pytest.fixture
def user(db):
    from girder.models.user import User

    return User().createUser(
        login="radadmin",
        password="password123",
        firstName="Rad",
        lastName="Admin",
        email="rad@example.com",
        admin=True,
    )


@pytest.fixture
def folder(db, user):
    from girder.models.folder import Folder

    return Folder().createFolder(
        parent=user, parentType="user", name="vol", creator=user
    )


def _spec_path(folder, taskId):
    return f"/folder/{folder['_id']}/volview_processing/tasks/{taskId}/spec"


# ---------------------------------------------------------------------------
# Happy path: a radiology id returns 200 + the translated spec JSON.
# ---------------------------------------------------------------------------


@pytest.mark.plugin("volview")
def test_radiology_task_spec_returns_200_translated_spec(
    server, user, folder, stub_slicer
):
    resp = server.request(path=_spec_path(folder, "radid"), method="GET", user=user)
    assert resp.output_status.startswith(b"200")
    body = resp.json
    # id is the CLI identity (cliItem.name), title is the XML <title>.
    assert body["id"] == "MedianFilter"
    assert body["title"] == "Median Filter"
    assert body["specVersion"] == 1
    # The wire body is exactly what the translator emits for this XML.
    assert body == slicer_spec.translate_slicer_xml(_MEDIAN_XML, "MedianFilter")


# ---------------------------------------------------------------------------
# Scope guard: a pathology / unknown id must 404 (the server is the boundary).
# ---------------------------------------------------------------------------


@pytest.mark.plugin("volview")
def test_pathology_task_spec_returns_404(server, user, folder, stub_slicer):
    resp = server.request(
        path=_spec_path(folder, "pathid"),
        method="GET",
        user=user,
        exception=True,
    )
    assert resp.output_status.startswith(b"404")


@pytest.mark.plugin("volview")
def test_unknown_task_spec_returns_404(server, user, folder, stub_slicer):
    resp = server.request(
        path=_spec_path(folder, "bogus"),
        method="GET",
        user=user,
        exception=True,
    )
    assert resp.output_status.startswith(b"404")
