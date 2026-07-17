"""Provenance self-cert for the vendored backend-contract copy.

``backend-contract/scripts/sync-backend.sh`` stamps ``SOURCE.txt`` recording the
client commit + contract version the copy was synced from, plus ``tree_sha256``
(the sha256 of ``MANIFEST.sha256``). This suite fails closed if the stamp is
absent/malformed or if ``tree_sha256`` disagrees with the vendored manifest --
i.e. the copy was hand-edited or partially synced.

LIMIT (honest): the backend tree has no independent view of the client, so this
CANNOT detect that the client regenerated the contract without re-syncing. That
whole-copy drift is caught only by the client's ``verify-backend.sh`` step, the
only checker that can see BOTH trees (dev machine / combined CI job).
"""

import hashlib

import contract_loader

_SOURCE_PATH = contract_loader.CONTRACT_ROOT / "SOURCE.txt"
_MANIFEST_PATH = contract_loader.CONTRACT_ROOT / "MANIFEST.sha256"
_REQUIRED_KEYS = ("contract_version", "client_git_sha", "tree_sha256")


def _parse_source(text):
    fields = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()
    return fields


def test_source_present_and_wellformed():
    assert _SOURCE_PATH.exists(), (
        "SOURCE.txt missing; the vendored copy was placed without the provenance "
        "stamp -- re-run backend-contract/scripts/sync-backend.sh"
    )
    fields = _parse_source(_SOURCE_PATH.read_text())
    for key in _REQUIRED_KEYS:
        assert fields.get(key), "SOURCE.txt missing/empty field: %s" % key


def test_source_synced_from_clean_client_tree():
    # sync-backend.sh refuses to run on a dirty contract subtree, so a truthful
    # stamp is always clean; a dirty stamp means the vendored artifacts cannot
    # be reconstructed from client_git_sha (an older sync or a bypassed guard).
    fields = _parse_source(_SOURCE_PATH.read_text())
    assert fields.get("client_git_dirty") == "false", (
        "SOURCE.txt records client_git_dirty=%s: the vendored copy was synced "
        "from uncommitted canonical-contract changes and its provenance is not "
        "reproducible -- commit the contract subtree and re-run sync-backend.sh"
        % fields.get("client_git_dirty")
    )


def test_source_tree_hash_matches_vendored_manifest():
    fields = _parse_source(_SOURCE_PATH.read_text())
    expected = fields["tree_sha256"]
    actual = hashlib.sha256(_MANIFEST_PATH.read_bytes()).hexdigest()
    assert actual == expected, (
        "SOURCE.txt tree_sha256 != sha256(MANIFEST.sha256): the vendored copy was "
        "tampered or partially synced -- re-run sync-backend.sh"
    )
