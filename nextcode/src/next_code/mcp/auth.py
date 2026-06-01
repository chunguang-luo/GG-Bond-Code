"""MCP OAuth 2.0 client — PKCE flow, dynamic registration, token refresh.

Supports the standard OAuth 2.0 Authorization Code flow with PKCE
(Proof Key for Code Exchange), which prevents authorization code interception.

Flow:
1. Discover OAuth endpoints from server metadata (RFC 8414, RFC 9728).
2. Register as a dynamic client if no client_id is configured (RFC 7591).
3. Generate PKCE code_verifier + code_challenge (S256).
4. Open browser for user authorization.
5. Exchange authorization code for tokens.
6. Store tokens securely.
7. Auto-refresh when access token expires.

Why not just store tokens in a file:
Tokens are sensitive — storing them in plaintext on disk is a security risk.
We use a dedicated directory with restricted permissions (0700) and plan to
migrate to platform-native keychain storage in a future iteration.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from .types import McpOAuthConfig

logger = logging.getLogger(__name__)

# Token storage directory (restricted permissions)
_TOKEN_DIR = Path.home() / ".nextcode" / "mcp-tokens"

# needs-auth cache TTL (15 minutes)
NEEDS_AUTH_CACHE_TTL_S = 15 * 60


class OAuthClient:
    """OAuth 2.0 client for MCP server authentication.

    Usage:
        client = OAuthClient(config)
        if client.needs_auth():
            tokens = await client.authorize()
        else:
            tokens = await client.load_tokens()
        headers = client.auth_headers(tokens)
    """

    def __init__(self, server_name: str, config: McpOAuthConfig) -> None:
        self._server_name = server_name
        self._config = config
        self._discovered: dict[str, str] = {}  # OAuth metadata cache
        self._needs_auth_cache_time: float | None = None

    async def discover_endpoints(self, server_url: str) -> dict[str, str]:
        """Discover OAuth endpoints via RFC 9728 / RFC 8414 metadata.

        Tries in order:
        1. Protected Resource Metadata (RFC 9728) at server_url/.well-known/oauth-protected-resource
        2. Authorization Server Metadata (RFC 8414) at the auth server's .well-known/oauth-authorization-server

        Returns discovered endpoints: {authorization_url, token_url, registration_url, ...}
        """
        if self._discovered:
            return self._discovered

        # Step 1: Try protected resource metadata
        parsed = urlparse(server_url)
        resource_metadata_url = f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource"

        auth_server_url = None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(resource_metadata_url)
                if resp.status_code == 200:
                    metadata = resp.json()
                    auth_server_url = metadata.get("authorization_servers", [None])[0]
        except Exception as e:
            logger.debug("Protected resource metadata lookup failed: %s", e)

        # Step 2: Authorization server metadata
        if auth_server_url:
            try:
                parsed_as = urlparse(auth_server_url)
                as_metadata_url = (
                    f"{parsed_as.scheme}://{parsed_as.netloc}"
                    f"/.well-known/oauth-authorization-server{parsed_as.path}"
                )
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(as_metadata_url)
                    if resp.status_code == 200:
                        self._discovered = resp.json()
                        return self._discovered
            except Exception as e:
                logger.debug("Authorization server metadata lookup failed: %s", e)

        # Fallback: use configured URLs directly
        self._discovered = {
            "authorization_endpoint": self._config.authorization_url,
            "token_endpoint": self._config.token_url,
        }
        return self._discovered

    async def register_client(self) -> tuple[str, str]:
        """Register as a dynamic OAuth client (RFC 7591).

        Returns (client_id, client_secret).
        """
        registration_url = self._discovered.get("registration_endpoint")
        if not registration_url:
            raise RuntimeError("No registration endpoint available for dynamic client registration")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(registration_url, json={
                "client_name": "NextCode",
                "redirect_uris": ["http://localhost:9876/callback"],
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "none",  # Public client
                "response_types": ["code"],
            })
            resp.raise_for_status()
            data = resp.json()
            return data["client_id"], data.get("client_secret", "")

    async def authorize(self) -> dict[str, str]:
        """Run the full OAuth 2.0 Authorization Code + PKCE flow.

        1. Generate PKCE code_verifier + code_challenge.
        2. Open browser for authorization.
        3. Start local HTTP server to receive callback.
        4. Exchange code for tokens.
        5. Store tokens.

        Returns token dict: {access_token, refresh_token, expires_at, ...}
        """
        endpoints = self._discovered
        auth_url = endpoints.get("authorization_endpoint")
        token_url = endpoints.get("token_endpoint")

        if not auth_url or not token_url:
            raise RuntimeError("OAuth endpoints not discovered. Call discover_endpoints() first.")

        # Get or register client
        client_id = self._config.client_id
        client_secret = self._config.client_secret
        if not client_id:
            client_id, client_secret = await self.register_client()

        # PKCE: generate code_verifier and code_challenge (S256)
        code_verifier = _generate_code_verifier()
        code_challenge = _generate_code_challenge(code_verifier)

        # Build authorization URL
        state = secrets.token_urlsafe(32)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost:9876/callback",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        if self._config.scopes:
            params["scope"] = " ".join(self._config.scopes)

        full_auth_url = f"{auth_url}?{urlencode(params)}"

        # Start local callback server
        callback_result: dict[str, str] = {}
        callback_event = asyncio.Event()

        async def _handle_callback(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                request_line = (await reader.readline()).decode()
                # Parse query params from GET /callback?code=...&state=...
                url_path = request_line.split(" ")[1] if " " in request_line else ""
                from urllib.parse import parse_qs
                params = parse_qs(urlparse(url_path).query)

                if params.get("state", [""])[0] != state:
                    body = b"OAuth error: state mismatch"
                elif "error" in params:
                    body = f"OAuth error: {params['error'][0]}".encode()
                elif "code" in params:
                    callback_result["code"] = params["code"][0]
                    body = b"Authorization successful! You can close this tab."
                else:
                    body = b"OAuth error: no code in response"

                writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
                await writer.drain()
            finally:
                writer.close()
                callback_event.set()

        server = await asyncio.start_server(_handle_callback, "127.0.0.1", 9876)

        # Open browser
        logger.info("Opening browser for MCP OAuth authorization: %s", self._server_name)
        import webbrowser
        webbrowser.open(full_auth_url)

        # Wait for callback (5 minute timeout)
        try:
            await asyncio.wait_for(callback_event.wait(), timeout=300.0)
        except asyncio.TimeoutError:
            raise RuntimeError("OAuth authorization timed out (5 minutes)")
        finally:
            server.close()
            await server.wait_closed()

        if "code" not in callback_result:
            raise RuntimeError("OAuth authorization failed: no code received")

        # Exchange code for tokens
        token_data = {
            "grant_type": "authorization_code",
            "code": callback_result["code"],
            "redirect_uri": "http://localhost:9876/callback",
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        if client_secret:
            token_data["client_secret"] = client_secret

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(token_url, data=token_data)
            resp.raise_for_status()
            tokens = resp.json()

        # Add expiry timestamp
        if "expires_in" in tokens:
            tokens["expires_at"] = time.time() + tokens["expires_in"]

        # Store tokens
        _store_tokens(self._server_name, tokens)

        # Clear needs-auth cache on successful auth
        self._needs_auth_cache_time = None

        return tokens

    async def load_tokens(self) -> dict[str, str] | None:
        """Load stored tokens for this server. Returns None if none stored."""
        return _load_tokens(self._server_name)

    async def refresh_tokens(self, refresh_token: str) -> dict[str, str]:
        """Refresh an expired access token using the refresh token.

        Returns new token dict with updated access_token and expires_at.
        """
        endpoints = self._discovered
        token_url = endpoints.get("token_endpoint", self._config.token_url)

        if not token_url:
            raise RuntimeError("No token endpoint available for refresh")

        client_id = self._config.client_id

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(token_url, data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            })
            resp.raise_for_status()
            tokens = resp.json()

        if "expires_in" in tokens:
            tokens["expires_at"] = time.time() + tokens["expires_in"]

        # Store updated tokens
        _store_tokens(self._server_name, tokens)

        return tokens

    def auth_headers(self, tokens: dict[str, str]) -> dict[str, str]:
        """Build Authorization header from tokens."""
        access_token = tokens.get("access_token", "")
        if not access_token:
            return {}
        return {"Authorization": f"Bearer {access_token}"}

    def is_needs_auth_cached(self) -> bool:
        """Check if this server was recently marked as needs-auth.

        15-minute cache to avoid repeated 401 round-trips.
        """
        if self._needs_auth_cache_time is None:
            return False
        return (time.time() - self._needs_auth_cache_time) < NEEDS_AUTH_CACHE_TTL_S

    def mark_needs_auth(self) -> None:
        """Mark this server as needing auth (starts the 15-minute cache)."""
        self._needs_auth_cache_time = time.time()

    def has_discovery_but_no_token(self) -> bool:
        """Check if server has OAuth discovery metadata but no stored token."""
        has_oauth = bool(self._config.authorization_url or self._config.resource_url_url)
        has_token = _load_tokens(self._server_name) is not None
        return has_oauth and not has_token


# ── PKCE Helpers ───────────────────────────────────────────────

def _generate_code_verifier() -> str:
    """Generate a PKCE code_verifier (43-128 chars, base64url-encoded)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def _generate_code_challenge(verifier: str) -> str:
    """Generate a PKCE code_challenge using S256 method (SHA-256 + base64url)."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


# ── Token Storage ──────────────────────────────────────────────

def _store_tokens(server_name: str, tokens: dict[str, Any]) -> None:
    """Store OAuth tokens for a server in a restricted directory."""
    _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    # Restrict directory permissions
    os.chmod(_TOKEN_DIR, 0o700)

    # Sanitize server name for filesystem
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in server_name)
    token_path = _TOKEN_DIR / f"{safe_name}.json"

    token_path.write_text(json.dumps(tokens, indent=2))
    os.chmod(token_path, 0o600)  # Owner read/write only


def _load_tokens(server_name: str) -> dict[str, Any] | None:
    """Load stored OAuth tokens for a server. Returns None if not found."""
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in server_name)
    token_path = _TOKEN_DIR / f"{safe_name}.json"

    if not token_path.exists():
        return None

    try:
        tokens = json.loads(token_path.read_text())
        # Check if expired (with 30s buffer)
        expires_at = tokens.get("expires_at", 0)
        if expires_at and time.time() > (expires_at - 30):
            # Try to refresh
            refresh_token = tokens.get("refresh_token")
            if refresh_token:
                return tokens  # Return stale tokens; caller should refresh
            return None
        return tokens
    except (json.JSONDecodeError, ValueError):
        return None
