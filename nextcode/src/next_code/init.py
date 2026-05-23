"""Core initialization — mirrors entrypoints/init.ts with memoize pattern."""

from __future__ import annotations

import functools
import logging
import sys
from typing import Optional

from .config.settings import load_settings, get_setting
from .config.auth import resolve_api_key

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def init() -> None:
    """One-time initialization. Called via Click pre-action hook."""
    # 1. Load settings (global + project)
    load_settings()

    # 2. Check required configuration — guide user through setup if missing
    model = get_setting("model", "")
    api_key = resolve_api_key()
    base_url = get_setting("base_url", "")

    if not api_key or not model or not base_url:
        print("\n  Welcome to NextCode! Let's configure your setup.\n")
        from .config.auth import configure_interactive
        configure_interactive()
        # Reload settings after interactive configuration
        load_settings()
        api_key = resolve_api_key()
        if not api_key:
            print("Error: API key is required. Run 'nextcode auth' to configure.", file=sys.stderr)
            sys.exit(1)

    # 3. API preconnection (warm TCP+TLS)
    _preconnect(api_key)


def _preconnect(api_key: str) -> None:
    """Send a lightweight HEAD request to warm the connection pool."""
    import httpx
    from .config.settings import get_setting

    base_url = get_setting("base_url")

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
