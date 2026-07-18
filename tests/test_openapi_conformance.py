"""The published OpenAPI is the backend's obligation surface.

The neutral REST OpenAPI (single-source-generated in VolView, read here from the
`volview` package's backend-contract) states exactly what a NON-girder backend
must implement. This is the backend-side half of the conformance kit: the contract
document stays neutral (grep-tested for girder route/id/enum/URL leaks — AC1),
declares exactly the neutral client-invoked operations, and the reference backend
implements every one of them.

Pure-stdlib (no server fixture / Mongo): it reads the contract JSON and checks
the module-level handlers exist.
"""

import json
import re

import contract_loader
from girder_volview.backend import routes

_OPENAPI = contract_loader.GENERATED_ROOT / "openapi.json"

# Neutral operationId -> the reference-backend handler that implements it. A NEW
# backend re-authors this mapping; for the reference backend every handler exists.
_OP_TO_HANDLER = {
    "listTasks": "listTasks",
    "getTaskSpec": "getTaskSpec",
    "runTask": "runTask",
    "listJobHistory": "listJobHistory",
    "getJobHistoryDetail": "getJobHistoryDetail",
    "deleteJob": "deleteJob",
    "stageInput": "stageInput",
    "getJob": "getJob",
    "getJobResults": "getJobResults",
    "cancelJob": "cancelJob",
}

# The declared-set mechanism: operations the contract declares whose backend
# handlers have not landed yet -- counted in the exact operation surface,
# excluded from the handler check. Empty; kept for the next contract-first
# surface growth.
_DECLARED_NOT_YET_IMPLEMENTED = frozenset()

# The neutrality gate: none of these girder-specifics may appear in the neutral
# document (mirrors the VolView-side openapi.spec.ts grep). `NeutralJobStatus` is
# allowed; the bare girder `JobStatus` enum name is not.
_FORBIDDEN = [
    ("girder route param folderId", re.compile(r"folderId")),
    ("girder api mount /api/v1", re.compile(r"/api/v1")),
    ("proxiable url shape", re.compile(r"proxiable", re.IGNORECASE)),
    ("girder mention", re.compile(r"girder", re.IGNORECASE)),
    ("slicer mention", re.compile(r"slicer", re.IGNORECASE)),
    ("backend task xml", re.compile(r"\bxml\b", re.IGNORECASE)),
    ("JobStatus enum name", re.compile(r"(?<!Neutral)JobStatus")),
    ("girder status INACTIVE", re.compile(r"\bINACTIVE\b")),
    ("girder status CANCELED", re.compile(r"\bCANCELED\b")),
    ("retired state succeeded", re.compile(r"succeeded", re.IGNORECASE)),
    ("retired state failed", re.compile(r"failed", re.IGNORECASE)),
    ("retired state queued", re.compile(r"\bqueued\b", re.IGNORECASE)),
]


def _load_openapi():
    return json.loads(_OPENAPI.read_text())


def _declared_operation_ids(doc):
    return {
        op["operationId"]
        for path_item in doc["paths"].values()
        for op in path_item.values()
    }


def test_openapi_present_and_is_openapi_3_1():
    assert _OPENAPI.exists()
    assert _load_openapi()["openapi"].startswith("3.1")


def test_openapi_declares_exactly_the_neutral_operations():
    expected = set(_OP_TO_HANDLER) | _DECLARED_NOT_YET_IMPLEMENTED
    assert _declared_operation_ids(_load_openapi()) == expected


def test_reference_backend_implements_every_declared_operation():
    for op_id in _declared_operation_ids(_load_openapi()):
        if op_id in _DECLARED_NOT_YET_IMPLEMENTED:
            continue
        handler = _OP_TO_HANDLER[op_id]
        assert callable(getattr(routes, handler)), handler


def test_job_addressed_routes_are_keyed_by_job_id_alone():
    doc = _load_openapi()
    for path, path_item in doc["paths"].items():
        for op in path_item.values():
            if op["operationId"] in (
                "getJob",
                "getJobResults",
                "cancelJob",
                "getJobHistoryDetail",
                "deleteJob",
            ):
                assert path.startswith("/jobs/{jobId}"), path
                assert "folderId" not in path


def test_openapi_leaks_nothing_girder_specific():
    serialized = json.dumps(_load_openapi())
    for label, pattern in _FORBIDDEN:
        assert not pattern.search(serialized), label
