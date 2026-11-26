"""The local web UI.

Optional: ``pip install "driftcatch[ui]"``. The core library never imports it.
"""

from __future__ import annotations

__all__ = ["serve"]


def serve(*args: object, **kwargs: object) -> None:
    """Lazy re-export so importing this package does not require FastAPI."""
    from driftcatch.ui.server import serve as _serve

    _serve(*args, **kwargs)  # type: ignore[arg-type]
