"""The proxiable load-handle scheme -- the ONE mint + parse pair.

This module owns the backend's file identity scheme -- the client-visible
handle string ``/<apiRoot>/file/<id>/proxiable/<name>``, which embeds the
backend file name and therefore MUST escape it -- with its mint and parse
COLOCATED so they can never drift apart again.

Before this module the handle scheme's mint (``utils.makeFileDownloadUrl``)
and parse (``inputs._fileIdFromMintedUri``) lived apart and disagreed: mint
embedded the raw file name while parse rejected any name containing ``/``,
``?`` or ``#`` -- so a legal Girder name like ``Lesion #1.seg.nrrd``
produced a handle the backend itself could not read back (processing submit
hard-400d on the backend's own mint). Both surfaces are now thin delegates
of this module.

**Format.** The name segment is
percent-encoded at mint (``urllib.parse.quote(name, safe="")`` --
byte-identical to JS ``encodeURIComponent``) and unescaped at parse, so
``parseFileHandle(mintFileHandle(fileId, name)) == (fileId, name)`` for
every legal name and the emitted handle carries no raw fragment/query
delimiter. Clients never decode: a handle is opaque, round-tripped
byte-for-byte; only the backend reads its own mint.

**Legacy compatibility.** Handles minted before the escaping fix embed the
RAW name (spaces, ``#``, ``?``) and are already in the wild -- stamped on
job records, held in live clients' echoes. Parse therefore accepts any
non-empty single-segment tail and unescapes it: the file id (the half every
runtime caller consumes) resolves identically for old and new mints, and
the two shapes canonicalize to the same identity. Genuinely foreign shapes
(wrong prefix, wrong resource, extra path segment, empty name, non-ObjectId
id) stay rejected, so callers' fail-closed / inert-passthrough behavior is
untouched.
"""

from urllib.parse import quote, unquote

from bson.objectid import ObjectId
from girder.utility.server import getApiRoot

_PROXIABLE_MARKER = "proxiable/"


def mintFileHandle(fileId, name):
    """Mint the proxiable load handle for a file id + backend file name.

    Origin-relative, keyed off the runtime ``getApiRoot()`` mount; the name
    segment is percent-encoded (RFC 3986, no safe characters) so reserved
    URL delimiters in legal Girder file names survive every wire context.
    """
    return "/" + "/".join(
        (
            getApiRoot(),
            "file",
            str(fileId),
            "proxiable",
            quote(str(name), safe=""),
        )
    )


def parseFileHandle(uri):
    """``(fileId, name)`` for a backend-minted load handle, or ``None``.

    The exact mirror of :func:`mintFileHandle` -- origin-relative
    ``/<apiRoot>/file/<24-hex-id>/proxiable/<name>`` against the same
    ``getApiRoot()`` mount, with the name segment unescaped. The tail must
    be one non-empty segment (no embedded ``/``), but is otherwise taken
    verbatim so legacy raw-name mints still resolve (see module docstring).
    Anything else returns ``None`` so callers fail closed and never
    dereference a foreign string.
    """
    if not isinstance(uri, str):
        return None
    prefix = "/" + getApiRoot() + "/file/"
    if not uri.startswith(prefix):
        return None
    fileId, sep, tail = uri[len(prefix) :].partition("/")
    if not sep or not ObjectId.is_valid(fileId):
        return None
    if not tail.startswith(_PROXIABLE_MARKER):
        return None
    name = tail[len(_PROXIABLE_MARKER) :]
    if not name or "/" in name:
        return None
    return fileId, unquote(name)
