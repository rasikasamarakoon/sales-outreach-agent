"""One-time OAuth handshake for the Zoho Mail MCP server.

The Zoho MCP console never shows you a token: the server is an OAuth 2.0
protected resource, and the token is minted for a *client* at connect time
rather than issued to you up front. Interactive clients do this dance silently
in the background. A scheduled agent has no browser at 9am, so Anthropic's
vault needs the access + refresh pair handed to it in advance -- after that it
refreshes on its own, forever.

This script performs that handshake once:

    discover metadata  ->  register a client (RFC 7591, no manual app setup)
                       ->  authorisation code + PKCE in your browser
                       ->  exchange for tokens  ->  write them into .env

    python zoho_oauth.py                 # minimal scopes, writes .env
    python zoho_oauth.py --print-only    # print instead of touching .env
    python zoho_oauth.py --scope "..."   # override the requested scopes

Requires ZOHO_MCP_URL in .env. Everything else is discovered.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import re
import secrets
import socket
import sys
import threading
import urllib.parse as up
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

import config as cfg

ROOT = Path(__file__).parent
ENV_FILE = ROOT / ".env"

# Least privilege: enough to create a draft, and nothing else. Deliberately
# excludes ZohoMail.messages.ALL -- that scope carries send, which would
# undercut the "agent drafts, human sends" rule at the token level no matter
# which tools the MCP server exposes.
DEFAULT_SCOPES = [
    "ZohoMCP.tool.execute",
    "ZohoMail.accounts.ALL",  # no narrower accounts scope is offered
    "ZohoMail.messages.CREATE",
    "ZohoMail.folders.READ",
]

# Scopes that would let a leaked token send or read mail. Refused unless the
# caller opts in explicitly.
SEND_CAPABLE = {"ZohoMail.messages.ALL", "ZohoMail.messages.UPDATE"}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover(mcp_url: str) -> tuple[dict, dict]:
    parts = up.urlsplit(mcp_url)
    base = f"{parts.scheme}://{parts.netloc}"

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resource = client.get(f"{base}/.well-known/oauth-protected-resource")
        resource.raise_for_status()
        resource_meta = resource.json()

        # The resource metadata names its authorisation servers; fall back to
        # the same host when the list is absent.
        servers = resource_meta.get("authorization_servers") or [base]
        auth = client.get(f"{servers[0].rstrip('/')}/.well-known/oauth-authorization-server")
        auth.raise_for_status()
        return resource_meta, auth.json()


# ---------------------------------------------------------------------------
# Dynamic client registration
# ---------------------------------------------------------------------------

def register(endpoint: str, redirect_uri: str, scope: str) -> dict:
    payload = {
        "client_name": "Trades Website Outreach Agent",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": scope,
    }
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        # Prefer a public client (PKCE, no secret to store). Some servers
        # insist on a confidential client, so fall back rather than fail.
        for method in ("none", "client_secret_post"):
            resp = client.post(endpoint, json={**payload, "token_endpoint_auth_method": method})
            if resp.status_code in (200, 201):
                return resp.json()
            print(f"  registration with token_endpoint_auth_method={method} "
                  f"-> HTTP {resp.status_code}")
        raise SystemExit(
            f"Dynamic client registration failed: {resp.status_code} {resp.text[:300]}"
        )


# ---------------------------------------------------------------------------
# Redirect listener
# ---------------------------------------------------------------------------

class _Callback(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self) -> None:  # noqa: N802  (stdlib naming)
        query = up.parse_qs(up.urlsplit(self.path).query)
        _Callback.result = {k: v[0] for k, v in query.items()}
        ok = "code" in _Callback.result

        if ok:
            heading = "Authorised."
            detail = "You can close this tab and return to the terminal."
        else:
            heading = "Authorisation failed."
            detail = _Callback.result.get(
                "error_description",
                _Callback.result.get("error", "No code returned."),
            )

        body = (
            "<html><body style='font-family:system-ui;padding:3rem;max-width:34rem'>"
            f"<h2>{heading}</h2><p>{detail}</p>"
            "</body></html>"
        ).encode("utf-8")

        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass  # keep the handshake output clean


def free_port() -> int:
    # Claim a port before registering, since the redirect URI is part of the
    # registration and must match exactly at the token exchange.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def await_callback(port: int, state: str, timeout: float = 300.0) -> str:
    server = http.server.HTTPServer(("127.0.0.1", port), _Callback)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout)
    server.server_close()

    result = _Callback.result
    if not result:
        raise SystemExit(f"No redirect received within {int(timeout)}s. Aborted.")
    if "error" in result:
        raise SystemExit(
            f"Zoho refused authorisation: {result['error']} — "
            f"{result.get('error_description', 'no detail given')}"
        )
    if result.get("state") != state:
        raise SystemExit("State mismatch on the redirect — aborting rather than trusting it.")
    return result["code"]


# ---------------------------------------------------------------------------
# .env writing
# ---------------------------------------------------------------------------

def update_env(values: dict[str, str]) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    remaining = dict(values)

    for i, line in enumerate(lines):
        match = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            lines[i] = f"{key}={remaining.pop(key)}"

    if remaining:
        lines.append("")
        lines.append("# --- Written by zoho_oauth.py ---")
        lines.extend(f"{k}={v}" for k, v in remaining.items())

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask(value: str) -> str:
    return f"{value[:6]}…{value[-4:]} ({len(value)} chars)" if len(value) > 14 else "set"


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def token_expired(skew_seconds: int = 300) -> bool:
    """True if the stored access token is missing, undated, or about to lapse."""
    if not cfg.ZOHO_ACCESS_TOKEN:
        return True
    if not cfg.ZOHO_TOKEN_EXPIRES_AT:
        return True  # undated: assume stale rather than send a token that may be dead
    try:
        expiry = datetime.strptime(
            cfg.ZOHO_TOKEN_EXPIRES_AT, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return expiry <= datetime.now(timezone.utc) + timedelta(seconds=skew_seconds)


def refresh_access_token(persist: bool = True) -> str:
    """Mint a fresh access token from the stored refresh token.

    Zoho issues one-hour access tokens, so the pair captured during the browser
    handshake is almost always stale by the time you get round to provisioning.
    Anthropic's vault refreshes on its own once the credential exists, but the
    credential can only be *created* with a token that is still valid.
    """
    if not (cfg.ZOHO_REFRESH_TOKEN and cfg.ZOHO_CLIENT_ID and cfg.ZOHO_TOKEN_ENDPOINT):
        raise SystemExit(
            "Cannot refresh: ZOHO_REFRESH_TOKEN, ZOHO_CLIENT_ID and "
            "ZOHO_TOKEN_ENDPOINT must all be set. Re-run: python zoho_oauth.py"
        )

    form = {
        "grant_type": "refresh_token",
        "refresh_token": cfg.ZOHO_REFRESH_TOKEN,
        "client_id": cfg.ZOHO_CLIENT_ID,
    }
    if cfg.ZOHO_CLIENT_SECRET:
        form["client_secret"] = cfg.ZOHO_CLIENT_SECRET

    resp = httpx.post(cfg.ZOHO_TOKEN_ENDPOINT, data=form, timeout=30.0,
                      headers={"Accept": "application/json"})
    if resp.status_code != 200:
        raise SystemExit(
            f"Refresh failed: HTTP {resp.status_code} {resp.text[:300]}\n"
            "The refresh token may have been revoked. Re-run: python zoho_oauth.py"
        )

    tokens = resp.json()
    access = tokens.get("access_token")
    if not access:
        raise SystemExit(f"No access_token in refresh response: {json.dumps(tokens)[:300]}")

    expires_at = ""
    if tokens.get("expires_in"):
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(tokens["expires_in"]))
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Update the in-process view so callers importing config see the new values.
    cfg.ZOHO_ACCESS_TOKEN = access
    cfg.ZOHO_TOKEN_EXPIRES_AT = expires_at
    values = {"ZOHO_ACCESS_TOKEN": access, "ZOHO_TOKEN_EXPIRES_AT": expires_at}

    # Some servers rotate the refresh token on every use; keep whichever is current.
    rotated = tokens.get("refresh_token")
    if rotated and rotated != cfg.ZOHO_REFRESH_TOKEN:
        cfg.ZOHO_REFRESH_TOKEN = rotated
        values["ZOHO_REFRESH_TOKEN"] = rotated

    if persist:
        update_env(values)
    return expires_at


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def rpc_body(resp: httpx.Response) -> dict | None:
    """Parse a JSON-RPC reply that may arrive as JSON or as an SSE frame."""
    text = resp.text
    if text.lstrip().startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
    return None


def verify() -> int:
    """Connect with the stored token and list the tools the server exposes."""
    if not cfg.ZOHO_ACCESS_TOKEN:
        raise SystemExit("No ZOHO_ACCESS_TOKEN in .env. Run: python zoho_oauth.py")

    headers = {
        "Authorization": f"Bearer {cfg.ZOHO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "zoho-oauth-verify", "version": "1"},
        },
    }

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.post(cfg.ZOHO_MCP_URL, headers=headers, json=init)
        print(f"initialize -> HTTP {resp.status_code}")
        if resp.status_code == 401:
            raise SystemExit("Token rejected. Re-run: python zoho_oauth.py")
        parsed = rpc_body(resp)
        if parsed and "result" in parsed:
            info = parsed["result"].get("serverInfo", {})
            print(f"  connected to {info.get('name', 'server')} {info.get('version', '')}".rstrip())
        elif resp.status_code >= 400:
            raise SystemExit(f"  {resp.text[:300]}")

        session = resp.headers.get("mcp-session-id")
        if session:
            headers["mcp-session-id"] = session
        client.post(cfg.ZOHO_MCP_URL, headers=headers,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})

        listed = client.post(
            cfg.ZOHO_MCP_URL, headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        parsed = rpc_body(listed)
        if not (parsed and "result" in parsed):
            raise SystemExit(f"tools/list -> HTTP {listed.status_code} {listed.text[:300]}")

        tools = parsed["result"].get("tools", [])
        print(f"\n{len(tools)} tool(s) exposed:")
        risky = []
        for tool in tools:
            name = tool.get("name", "?")
            summary = (tool.get("description") or "").split("\n")[0][:70]
            print(f"  {name:<34} {summary}")
            if any(k in name.lower() for k in ("send", "reply", "forward", "delete", "trash")):
                risky.append(name)

        if risky:
            print("\n  !! Send/delete-capable tools are reachable:")
            for name in risky:
                print(f"       {name}")
            print("  The system prompt tells the agent to refuse them, but the safer")
            print("  fix is to remove them from the server in the Zoho MCP console.")
        else:
            print("\n  OK: no send/reply/delete tool exposed.")
    return 0


# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", help="space-separated scopes (default: least privilege)")
    parser.add_argument("--print-only", action="store_true",
                        help="print the .env lines instead of writing them")
    parser.add_argument("--allow-send", action="store_true",
                        help="permit send-capable scopes (the agent is not meant to send)")
    parser.add_argument("--verify", action="store_true",
                        help="test the stored token and list the server's tools")
    parser.add_argument("--refresh", action="store_true",
                        help="mint a fresh access token from the stored refresh token")
    args = parser.parse_args(argv[1:])

    if not cfg.ZOHO_MCP_URL:
        raise SystemExit("ZOHO_MCP_URL is not set in .env.")

    if args.refresh:
        expires_at = refresh_access_token()
        print(f"Access token refreshed. Valid until {expires_at or 'unstated'}.")
        return 0

    if args.verify:
        if token_expired():
            print("Stored token is stale; refreshing before verifying...")
            refresh_access_token()
        return verify()

    scopes = args.scope.split() if args.scope else list(DEFAULT_SCOPES)
    risky = SEND_CAPABLE.intersection(scopes)
    if risky and not args.allow_send:
        raise SystemExit(
            f"Refusing to request send-capable scope(s): {', '.join(sorted(risky))}.\n"
            "The agent is designed to draft only. Pass --allow-send to override."
        )
    scope = " ".join(scopes)

    print("Discovering OAuth metadata...")
    resource_meta, auth_meta = discover(cfg.ZOHO_MCP_URL)
    token_endpoint = auth_meta["token_endpoint"]
    print(f"  authorisation {auth_meta['authorization_endpoint']}")
    print(f"  token         {token_endpoint}")

    if "S256" not in (auth_meta.get("code_challenge_methods_supported") or ["S256"]):
        raise SystemExit("Server does not advertise PKCE S256; refusing to continue.")

    port = free_port()
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    print("\nRegistering a client...")
    registration = register(auth_meta["registration_endpoint"], redirect_uri, scope)
    client_id = registration["client_id"]
    client_secret = registration.get("client_secret", "")
    print(f"  client_id     {client_id}")
    print(f"  client_secret {'issued' if client_secret else 'none (public client + PKCE)'}")

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    state = secrets.token_urlsafe(24)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # RFC 8707: bind the token to this specific MCP endpoint.
        "resource": resource_meta.get("resource", cfg.ZOHO_MCP_URL),
    }
    authorize_url = f"{auth_meta['authorization_endpoint']}?{up.urlencode(params)}"

    print("\nRequesting these scopes:")
    for item in scopes:
        print(f"    {item}")
    print(f"\nOpening your browser. Log in to Zoho and click Allow.")
    print(f"If nothing opens, paste this into a browser:\n\n{authorize_url}\n")
    webbrowser.open(authorize_url)

    print(f"Listening on {redirect_uri} ...")
    code = await_callback(port, state)
    print("  authorisation code received")

    print("\nExchanging the code for tokens...")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": params["resource"],
    }
    if client_secret:
        form["client_secret"] = client_secret

    resp = httpx.post(token_endpoint, data=form, timeout=30.0,
                      headers={"Accept": "application/json"})
    if resp.status_code != 200:
        raise SystemExit(f"Token exchange failed: HTTP {resp.status_code} {resp.text[:400]}")
    tokens = resp.json()

    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token", "")
    if not access:
        raise SystemExit(f"No access_token in the response: {json.dumps(tokens)[:300]}")
    if not refresh:
        print("  WARNING: no refresh_token returned. Anthropic cannot auto-refresh,")
        print("           so the agent will lose access when this token expires.")

    expires_at = ""
    if tokens.get("expires_in"):
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(tokens["expires_in"]))
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"  access_token  {mask(access)}")
    print(f"  refresh_token {mask(refresh) if refresh else 'not issued'}")
    print(f"  expires_at    {expires_at or 'not stated'}")

    values = {
        "ZOHO_AUTH_MODE": "mcp_oauth",
        "ZOHO_CLIENT_ID": client_id,
        "ZOHO_CLIENT_SECRET": client_secret,
        "ZOHO_TOKEN_ENDPOINT": token_endpoint,
        "ZOHO_ACCESS_TOKEN": access,
        "ZOHO_REFRESH_TOKEN": refresh,
        "ZOHO_TOKEN_EXPIRES_AT": expires_at,
    }

    if args.print_only:
        print("\nAdd these to .env:\n")
        for key, value in values.items():
            print(f"{key}={value}")
    else:
        update_env(values)
        print(f"\nWritten to {ENV_FILE} (secrets not echoed above).")

    print(
        "\nNext: verify the server accepts the token and see which tools it exposes:\n"
        "  python zoho_oauth.py --verify\n"
        "then provision the agent:\n"
        "  python setup_agent.py\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
