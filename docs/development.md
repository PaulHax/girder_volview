# Development

## Backend contract and tests

The backend's conformance tests validate the server against VolView's
`backend-contract` — the ONE normative copy of the wire fixtures + generated
JSON Schemas. This repo keeps **no vendored copy**; the tests read the contract
from wherever the `volview` dependency is installed
(`girder_volview/web_client/node_modules/volview/backend-contract`, shipped in
the package's `files`).

- **Against the pinned release** — fetch the pinned `volview`, then run the
  suite:

  ```sh
  npm --prefix girder_volview/web_client install
  tox -e test        # or: pytest
  ```

- **Against an unreleased VolView branch** (developing the two together) — link
  a local VolView checkout so the tests read that branch's contract:

  ```sh
  cd <VolView checkout> && npm link
  npm --prefix girder_volview/web_client link volview
  ```

  Or point the tests straight at a checkout, no link required:

  ```sh
  GIRDER_VOLVIEW_CONTRACT_DIR=<VolView checkout>/backend-contract pytest
  ```

CI installs the **pinned published** `volview`, so when the backend is developed
ahead of the latest published contract the conformance tests are expected to be
red; they go green once VolView publishes (a merge-to-main dev release) and the
`volview` pin here is bumped to it.
