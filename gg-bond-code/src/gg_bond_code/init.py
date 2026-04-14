"""Core initialization — mirrors entrypoints/init.ts with memoize pattern."""

from __future__ import annotations

import functools
import sys
from typing import Optional

from gg_bond_code.config.settings import load_settings
from gg_bond_code.config.auth import resolve_api_key


@functools.lru_cache(maxsize=1)
def init() -> None:
    """One-time initialization. Called via Click pre-action hook."""
    # 1. Load settings (global + project)
    load_settings()

    # 2. Resolve API key
    api_key = resolve_api_key()
    if not api_key:
        print(
            "Error: No API key found. Set GGBOND_API_KEY or run 'ggbond auth'.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 3. API preconnection (warm TCP+TLS)
    _preconnect(api_key)


def _preconnect(api_key: str) -> None:
    """Send a lightweight HEAD request to warm the connection pool."""
    import httpx

    from gg_bond_code.api.client import _DEFAULT_BASE_URLS, _model_family
    from gg_bond_code.config.settings import get_setting

    model = get_setting("model", "deepseek-chat")
    family = _model_family(model)
    base_url = get_setting("base_url") or _DEFAULT_BASE_URLS[family]

    try:
        import threading

        def _do_preconnect():
            try:
                with httpx.Client(timeout=2.0) as client:
                    client.head(base_url)
            except Exception:
                pass

        t = threading.Thread(target=_do_preconnect, daemon=True)
        t.start()
    except ImportError:
        pass
