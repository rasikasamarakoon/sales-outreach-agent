"""One-time provisioning for the trades website-outreach agent.

Creates (or updates, if state.json already has IDs):

    environment  ->  memory store  ->  vault + credentials
                 ->  researcher agent  ->  coordinator agent
                 ->  scheduled deployment (09:00 Pacific/Auckland, daily)

Safe to re-run: existing resources are updated in place rather than duplicated,
so editing prompts/system_prompt.md and re-running publishes a new agent
version without touching the schedule.

    python setup_agent.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import anthropic

import config as cfg
import zoho_oauth
from anthropic_compat import create_deployment, update_deployment

ROOT = Path(__file__).parent


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def render(template_path: Path, **values: str) -> str:
    text = template_path.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def email_template() -> str:
    """The approved outreach email, inlined into the system prompt.

    Kept as its own file so the copy can be edited without touching the
    agent's instructions; re-running this script republishes the agent with
    whatever the file currently says.
    """
    path = ROOT / "email_templates" / cfg.EMAIL_TEMPLATE_FILE
    if not path.exists():
        raise SystemExit(
            f"Email template not found: {path}\n"
            "Every draft is built from it, so provisioning cannot continue."
        )
    return path.read_text(encoding="utf-8").strip()


def system_prompt() -> str:
    return render(
        ROOT / "prompts" / "system_prompt.md",
        SENDER_COMPANY=cfg.SENDER_COMPANY,
        PROSPECTS_PER_DAY=str(cfg.PROSPECTS_PER_DAY),
        EMAIL_TEMPLATE=email_template(),
    )


def daily_task() -> str:
    return render(
        ROOT / "prompts" / "daily_task.md",
        PROSPECTS_PER_DAY=str(cfg.PROSPECTS_PER_DAY),
        NICHES="\n".join(f"- {n}" for n in cfg.NICHES),
        REGIONS="\n".join(f"- {r}" for r in cfg.REGIONS),
        SENDER_COMPANY=cfg.SENDER_COMPANY,
        SENDER_EMAIL=cfg.SENDER_EMAIL,
        SENDER_WEBSITE=cfg.SENDER_WEBSITE,
        UNSUBSCRIBE_LINE=cfg.UNSUBSCRIBE_LINE,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def preflight() -> None:
    missing = [
        name
        for name in (
            "SENDER_NAME", "SENDER_TITLE", "SENDER_COMPANY", "SENDER_EMAIL",
            "SENDER_WEBSITE",
        )
        if not getattr(cfg, name)
    ]
    if missing:
        raise SystemExit(
            "These must be set in .env — they go into every draft, and the UEM Act "
            "requires the sender to be identified accurately and be readily "
            "contactable:\n  " + "\n  ".join(missing)
        )
    if not cfg.ZOHO_MCP_URL:
        raise SystemExit(
            "ZOHO_MCP_URL is not set. Create a server at https://www.zoho.com/mcp/, "
            "add the Zoho Mail draft tools, authorise it, and copy the server URL "
            "into .env."
        )
    if cfg.ZOHO_AUTH_MODE == "url_embedded":
        # Nothing to store: vault credentials are substituted into request
        # headers and bodies at egress, never into the URL, so a key that lives
        # in ZOHO_MCP_URL cannot be vaulted. It travels as part of the agent's
        # mcp_servers entry instead — see the auth note in README.md.
        pass
    elif cfg.ZOHO_AUTH_MODE == "static_bearer":
        if not cfg.ZOHO_ACCESS_TOKEN:
            raise SystemExit(
                "ZOHO_AUTH_MODE=static_bearer requires ZOHO_ACCESS_TOKEN. If your "
                "Zoho server URL already carries the key, set "
                "ZOHO_AUTH_MODE=url_embedded instead."
            )
    elif cfg.ZOHO_AUTH_MODE == "mcp_oauth":
        if not (cfg.ZOHO_ACCESS_TOKEN and cfg.ZOHO_REFRESH_TOKEN and cfg.ZOHO_CLIENT_ID):
            raise SystemExit(
                "ZOHO_AUTH_MODE=mcp_oauth requires ZOHO_ACCESS_TOKEN, "
                "ZOHO_REFRESH_TOKEN and ZOHO_CLIENT_ID."
            )
    else:
        raise SystemExit(
            f"Unknown ZOHO_AUTH_MODE={cfg.ZOHO_AUTH_MODE!r}. Expected one of: "
            "url_embedded, static_bearer, mcp_oauth."
        )


# ---------------------------------------------------------------------------
# Resource builders
# ---------------------------------------------------------------------------

def ensure_environment(client, state: dict) -> str:
    if state.get("environment_id"):
        return state["environment_id"]
    env = client.beta.environments.create(
        name=cfg.ENVIRONMENT_NAME,
        description="Sandbox for trades prospect research and email drafting.",
        # Prospecting means fetching arbitrary NZ business websites, so egress
        # can't be enumerated in advance.
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    state["environment_id"] = env.id
    print(f"  environment   {env.id}")
    return env.id


def ensure_memory_store(client, state: dict) -> str:
    if state.get("memory_store_id"):
        return state["memory_store_id"]
    store = client.beta.memory_stores.create(
        name=cfg.MEMORY_STORE_NAME,
        description=(
            "Outreach history for New Zealand trades prospecting. "
            "contacted/index/<YYYY-MM>.md holds one terse line per business "
            "already emailed and is the dedup source of truth — grep it, never "
            "list the whole tree. contacted/detail/<YYYY-MM>/<domain>.md holds "
            "the full record and is pruned after the retention window. "
            "excluded/index.md lists businesses permanently ruled out. "
            "niches/rotation.md tracks which trades and regions were used on "
            "which dates. playbook/learnings.md holds what is working."
        ),
    )
    state["memory_store_id"] = store.id
    print(f"  memory store  {store.id}")
    return store.id


def zoho_auth_block() -> dict:
    if cfg.ZOHO_AUTH_MODE == "mcp_oauth":
        refresh = {
            "token_endpoint": cfg.ZOHO_TOKEN_ENDPOINT,
            "client_id": cfg.ZOHO_CLIENT_ID,
            "refresh_token": cfg.ZOHO_REFRESH_TOKEN,
            "token_endpoint_auth": (
                {"type": "client_secret_post", "client_secret": cfg.ZOHO_CLIENT_SECRET}
                if cfg.ZOHO_CLIENT_SECRET
                else {"type": "none"}
            ),
        }
        auth = {
            "type": "mcp_oauth",
            "mcp_server_url": cfg.ZOHO_MCP_URL,
            "access_token": cfg.ZOHO_ACCESS_TOKEN,
            "refresh": refresh,
        }
        if cfg.ZOHO_TOKEN_EXPIRES_AT:
            auth["expires_at"] = cfg.ZOHO_TOKEN_EXPIRES_AT
        return auth
    return {
        "type": "static_bearer",
        "mcp_server_url": cfg.ZOHO_MCP_URL,
        "token": cfg.ZOHO_ACCESS_TOKEN,
    }


ZOHO_CREDENTIAL_NAME = "Zoho Mail MCP"
NZBN_CREDENTIAL_NAME = "NZBN API subscription key"


def refresh_fingerprint() -> str:
    """Short digest of the current refresh token, so state.json can tell
    whether the vault's write-only copy is still the same one without ever
    storing the secret itself."""
    if not cfg.ZOHO_REFRESH_TOKEN:
        return ""
    return hashlib.sha256(cfg.ZOHO_REFRESH_TOKEN.encode()).hexdigest()[:16]


def existing_credentials(client, vault_id: str) -> dict[str, str]:
    """Map display name -> credential id for what is already in the vault.

    Credentials are created one call after the vault itself, so a failure in
    between leaves a vault with nothing in it. Listing first makes a re-run
    repair that instead of skipping straight past it.
    """
    try:
        return {
            getattr(item, "display_name", ""): item.id
            for item in client.beta.vaults.credentials.list(vault_id)
        }
    except Exception as exc:  # listing is best-effort; fall back to creating
        print(f"    (could not list existing credentials: {exc})")
        return {}


def ensure_vault(client, state: dict) -> str:
    if state.get("vault_id"):
        vault_id = state["vault_id"]
    else:
        vault = client.beta.vaults.create(
            display_name=cfg.VAULT_NAME,
            metadata={"purpose": "trades-website-outreach"},
        )
        vault_id = state["vault_id"] = vault.id
        print(f"  vault         {vault.id}")

    present = existing_credentials(client, vault_id)

    if cfg.ZOHO_AUTH_MODE == "url_embedded":
        # The URL authenticates itself; a credential here would never match.
        print("    - Zoho MCP credential skipped (key is inside ZOHO_MCP_URL)")
    elif ZOHO_CREDENTIAL_NAME in present:
        # The vault refreshes the access token itself, so a matching refresh
        # token means there is nothing to do. Only if Zoho rotated it locally
        # does the vault's copy go stale -- and a stale refresh token fails
        # silently weeks later, mid-run, rather than here.
        if state.get("zoho_refresh_fingerprint") == refresh_fingerprint():
            print("    = Zoho MCP credential present and current")
        else:
            print("    ! The refresh token in .env no longer matches the one in the")
            print("      vault, so the vault will lose access when its token lapses.")
            print("      Credential auth cannot be updated in place (client_id and")
            print("      token_endpoint are immutable). Archive credential")
            print(f"      {present[ZOHO_CREDENTIAL_NAME]} in the Console, then re-run.")
    else:
        client.beta.vaults.credentials.create(
            vault_id,
            display_name=ZOHO_CREDENTIAL_NAME,
            auth=zoho_auth_block(),
        )
        state["zoho_refresh_fingerprint"] = refresh_fingerprint()
        print("    + Zoho MCP credential")

    if cfg.NZBN_API_KEY and NZBN_CREDENTIAL_NAME in present:
        print("    = NZBN_API_KEY credential already present")
    elif cfg.NZBN_API_KEY:
        # Substituted at egress — the sandbox only ever sees a placeholder, and
        # only on requests to the NZBN host. Header-only: it's a header key.
        client.beta.vaults.credentials.create(
            vault_id,
            display_name=NZBN_CREDENTIAL_NAME,
            auth={
                "type": "environment_variable",
                "secret_name": "NZBN_API_KEY",
                "secret_value": cfg.NZBN_API_KEY,
                "networking": {
                    "type": "limited",
                    "allowed_hosts": ["api.business.govt.nz"],
                },
                "injection_location": {"header": True},
            },
        )
        print("    + NZBN_API_KEY environment credential")
    return vault_id


RESEARCHER_SYSTEM = """\
You research one New Zealand trade or contracting business at a time and report
back.

Given a business name, or a trade-and-region brief, find out what web presence
the business currently has and report concisely:

- Legal/trading name, region, and the trade they work in
- **Whether they have a website at all.** Search their name plus their town. If
  only Facebook, Google Business Profile, or directory listings come back, say
  so plainly and name the listings you did find.
- If a website exists: the domain, whether it renders on a phone, the most
  recent date visible on it, whether the gallery and contact form actually
  work, and whether it sits on a free-host subdomain. Say plainly whether it
  looks current or dated.
- Roughly how many staff, and what they actually sell
- Any email address the business has PUBLISHED itself — on its own site, its
  own Facebook or Google Business Profile, or a directory listing it created —
  quoted exactly, with the URL of the page you found it on. Never guess or
  construct an address.
- Any statement declining unsolicited commercial email, quoted if present
- Evidence they are currently trading (dated post, job ad, recent reviews)

Report findings with a source URL for every claim. If you cannot find a
published email address, say so plainly rather than inferring one. If their
website is good and current, say that plainly too — it is a useful finding, not
a failure. Do not write marketing copy and do not draft emails — findings only.
"""


def researcher_tools() -> list[dict]:
    return [
        {
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": False},
            "configs": [
                {"name": name, "enabled": True}
                for name in ("read", "glob", "grep", "web_fetch", "web_search")
            ],
        }
    ]


def ensure_researcher(client, state: dict) -> str | None:
    if not cfg.USE_MULTIAGENT:
        return None
    if state.get("researcher_agent_id"):
        agent = client.beta.agents.update(
            state["researcher_agent_id"],
            system=RESEARCHER_SYSTEM,
            model=cfg.RESEARCHER_MODEL,
            tools=researcher_tools(),
        )
        print(f"  researcher    {agent.id} (v{agent.version})")
        return agent.id
    agent = client.beta.agents.create(
        name=cfg.RESEARCHER_AGENT_NAME,
        description=(
            "Fast, low-cost, read-only researcher for a single NZ trade business. "
            "Give it one company or one trade-and-region brief; it establishes what "
            "web presence the business has and reports findings with a source URL "
            "for every claim, including any published contact email. Spawn several "
            "in parallel."
        ),
        model=cfg.RESEARCHER_MODEL,
        system=RESEARCHER_SYSTEM,
        tools=researcher_tools(),
    )
    state["researcher_agent_id"] = agent.id
    print(f"  researcher    {agent.id} (v{agent.version})")
    return agent.id


def coordinator_payload(researcher_id: str | None) -> dict:
    payload: dict = {
        "model": {"id": cfg.COORDINATOR_MODEL, "effort": cfg.EFFORT},
        "system": system_prompt(),
        "tools": [
            {"type": "agent_toolset_20260401", "default_config": {"enabled": True}},
            # Deny-by-default over the Zoho server. It exposes nine tools, two of
            # which send mail outright; the agent only needs to look up the
            # account ID and write drafts. Everything not named here — including
            # ZohoMail_sendReplyEmail, readMessages and moveMessages — is
            # unreachable regardless of what the model decides to try.
            # permission_policy must be always_allow: MCP tools default to
            # "ask", which parks the session waiting for a client-side
            # confirmation. A scheduled 9am run has no client attached, so the
            # default would hang every morning instead of drafting. The guard
            # against sending is the allowlist plus the prompt, not this.
            {
                "type": "mcp_toolset",
                "mcp_server_name": cfg.ZOHO_MCP_NAME,
                "default_config": {
                    "enabled": False,
                    "permission_policy": {"type": "always_allow"},
                },
                "configs": [
                    {
                        "name": name,
                        "enabled": True,
                        "permission_policy": {"type": "always_allow"},
                    }
                    for name in cfg.ZOHO_ALLOWED_TOOLS
                ],
            },
        ],
        "mcp_servers": [
            {"type": "url", "name": cfg.ZOHO_MCP_NAME, "url": cfg.ZOHO_MCP_URL}
        ],
    }
    if researcher_id:
        payload["multiagent"] = {
            "type": "coordinator",
            "agents": [researcher_id, {"type": "self"}],
        }
    return payload


def ensure_coordinator(client, state: dict, researcher_id: str | None) -> str:
    payload = coordinator_payload(researcher_id)
    if state.get("agent_id"):
        agent = client.beta.agents.update(state["agent_id"], **payload)
        print(f"  agent         {agent.id} (v{agent.version})")
        return agent.id
    agent = client.beta.agents.create(
        name=cfg.AGENT_NAME,
        description="Daily NZ trades prospecting and Zoho Mail draft generation.",
        **payload,
    )
    state["agent_id"] = agent.id
    print(f"  agent         {agent.id} (v{agent.version})")
    return agent.id


def deployment_payload(state: dict) -> dict:
    return {
        "agent": state["agent_id"],
        "environment_id": state["environment_id"],
        "initial_events": [
            {"type": "user.message", "content": [{"type": "text", "text": daily_task()}]}
        ],
        "resources": [
            {
                "type": "memory_store",
                "memory_store_id": state["memory_store_id"],
                "access": "read_write",
                "instructions": (
                    "Outreach history. Before researching any candidate, grep "
                    "contacted/index/ and excluded/index.md for its domain — a hit "
                    "in either means skip it. Never list or read the whole "
                    "contacted/ tree; it grows every day. Read niches/rotation.md "
                    "to pick trades you have not used recently. Write the index "
                    "line and detail file for each business immediately after its "
                    "draft is created, not in a batch at the end."
                ),
            }
        ],
        "vault_ids": [state["vault_id"]],
        "budget": {
            "type": "limit",
            "max_list_cost": {"amount": cfg.DAILY_BUDGET_CENTS, "currency": "USD"},
        },
        "schedule": {
            "type": "cron",
            "expression": cfg.CRON_EXPRESSION,
            "timezone": cfg.TIMEZONE,
        },
    }


def ensure_deployment(client, state: dict) -> str:
    body = deployment_payload(state)
    if state.get("deployment_id"):
        result = update_deployment(client, state["deployment_id"], **body)
        print(f"  deployment    {state['deployment_id']} (updated)")
    else:
        result = create_deployment(client, name=cfg.DEPLOYMENT_NAME, **body)
        state["deployment_id"] = result["id"]
        print(f"  deployment    {result['id']}")

    upcoming = (result.get("schedule") or {}).get("upcoming_runs_at") or []
    if upcoming:
        print("  next runs (UTC):")
        for stamp in upcoming[:3]:
            print(f"    - {stamp}")
    return state["deployment_id"]


# ---------------------------------------------------------------------------

def main() -> int:
    preflight()

    # Zoho issues one-hour access tokens, so the pair captured during the
    # browser handshake is usually stale by the time you provision. The vault
    # refreshes on its own once the credential exists, but creating it requires
    # a token that is still live -- otherwise the API rejects expires_at.
    if cfg.ZOHO_AUTH_MODE == "mcp_oauth" and zoho_oauth.token_expired():
        print("Zoho access token is stale; refreshing before provisioning...")
        expires_at = zoho_oauth.refresh_access_token()
        print(f"  refreshed, valid until {expires_at or 'unstated'}\n")

    client = anthropic.Anthropic()
    state = cfg.load_state()

    print("Provisioning trades website-outreach agent...")
    try:
        ensure_environment(client, state)
        ensure_memory_store(client, state)
        ensure_vault(client, state)
        researcher_id = ensure_researcher(client, state)
        ensure_coordinator(client, state, researcher_id)
        ensure_deployment(client, state)
    finally:
        # Persist whatever succeeded so a re-run doesn't orphan resources.
        cfg.save_state(state)

    print(
        f"\nDone. Scheduled for {cfg.CRON_EXPRESSION} {cfg.TIMEZONE}.\n"
        "IDs saved to state.json — keep it (or move it to your secrets store).\n\n"
        "Test it now without waiting for 9am:\n"
        "  python run_now.py\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
