"""Shared test scaffolding.

Home for helpers the Mongo-backed route suites all need. ``mongo_reachable`` was
copy-pasted byte-for-byte into a dozen ``test_*`` modules to drive their
module-level ``pytestmark`` self-skip; it lives here now and each module imports
it (``from conftest import mongo_reachable``) while keeping its own skip reason.
"""

import os
import socket


def mongo_reachable(timeout=0.5):
    """Whether a live test Mongo is reachable (drives the route suites' self-skip).

    Reads ``GIRDER_TEST_DB`` for a non-default host/port, defaulting to
    ``localhost:27017``, and probes it with a short-timeout TCP connect so the
    Mongo-backed route tests skip cleanly offline instead of erroring.
    """
    host, port = "localhost", 27017
    uri = os.environ.get("GIRDER_TEST_DB", "")
    if uri.startswith("mongodb://"):
        netloc = uri[len("mongodb://") :].split("/", 1)[0].split(",", 1)[0]
        if ":" in netloc:
            host, port_str = netloc.rsplit(":", 1)
            port = int(port_str) if port_str.isdigit() else port
        elif netloc:
            host = netloc
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
