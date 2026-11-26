"""Single source of truth for the project's identity.

Everything that needs the package or CLI name imports it from here, so renaming
the project is a one-line change.
"""

from __future__ import annotations

NAME = "driftcatch"
CLI_NAME = "drift"
RECORDING_VERSION = 1
ENV_PREFIX = "DRIFTCATCH"
