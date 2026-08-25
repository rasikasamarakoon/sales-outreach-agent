"""Central configuration for the trades website-outreach agent.

DEMO BUILD. The sender identity, the offer, and every credential in .env are
placeholders — see README.md. Everything that changes between environments
lives here or in .env. IDs created by setup_agent.py are written to
state.json — never hardcode them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------
# 9:00 AM New Zealand time, every day. "Pacific/Auckland" is the IANA zone —
# the platform matches wall-clock time, so this stays 9am through NZDT/NZST.
CRON_EXPRESSION = "0 9 * * *"
TIMEZONE = "Pacific/Auckland"

# --------------------------------------------------------------------------
# Model + spend
# --------------------------------------------------------------------------
COORDINATOR_MODEL = "claude-opus-5"
RESEARCHER_MODEL = "claude-haiku-4-5"  # cheap, reading-heavy worker
EFFORT = "high"  # sweep low/medium/high once you have a few runs to compare

# Hard ceiling per daily run, in cents. "1000" = USD $10.00 of list-priced
# spend. The session pauses at the cap rather than terminating; raise it if runs
# are consistently truncating before 10 prospects are done.
#
# 500 is not enough for a 10-prospect run: the cap tends to land around the
# ninth draft, and the CSV/markdown output files are written last, so they are
# what gets lost.
DAILY_BUDGET_CENTS = "1000"

# Fan research out to cheap worker threads instead of reading 10 sites in one
# context. Set False for a single-threaded agent (simpler traces, higher cost).
USE_MULTIAGENT = True

# --------------------------------------------------------------------------
# Targeting
# --------------------------------------------------------------------------
PROSPECTS_PER_DAY = 10

# The approved outreach email, in email_templates/. Every draft is this file
# with two substitutions — the greeting name and the one observation line — so
# editing it and re-running setup_agent.py changes the copy for every future
# run without touching the agent's instructions.
EMAIL_TEMPLATE_FILE = "outreach_email_template.md"

# The agent picks a mixed slate from this list each day and records which
# niches it used in the memory store, so the mix rotates instead of repeating.
NICHES = [
    "plumbing and gasfitting contractors",
    "electrical contractors",
    "roofing and spouting contractors",
    "residential builders and renovation contractors",
    "painting and decorating contractors",
    "landscaping and garden construction",
    "concrete, paving and driveway contractors",
    "fencing and gate installers",
    "heat pump and HVAC installers",
    "tiling and waterproofing contractors",
    "carpentry and joinery workshops",
    "glazing and window installers",
    "drainlaying and excavation contractors",
    "scaffolding contractors",
    "arborists and tree services",
    "kitchen and bathroom fit-out specialists",
    "plastering and gib-stopping contractors",
    "flooring installers (carpet, vinyl, timber)",
    "garage door supply and installation",
    "solar and battery installers",
    "insulation installers",
    "security and alarm installers",
    "pool construction and servicing",
    "demolition and site clearing contractors",
    "earthmoving and civil contractors",
    "welding and metal fabrication workshops",
    "irrigation and pump servicing",
    "septic tank and wastewater contractors",
    "asbestos removal and remediation",
    "appliance and whiteware repair technicians",
]

REGIONS = [
    "Auckland", "Wellington", "Christchurch", "Hamilton", "Tauranga",
    "Dunedin", "Palmerston North", "Napier-Hastings", "Nelson", "Queenstown",
    "Rotorua", "New Plymouth", "Invercargill", "Whangarei",
]

# --------------------------------------------------------------------------
# Memory retention
# --------------------------------------------------------------------------
# `contacted/index/<YYYY-MM>.md` is permanent — it is what stops a business
# being emailed twice, it is one terse line per contact, and it is cheap to
# grep. Only the fuller `contacted/detail/<YYYY-MM>/` records expire.
#
# Detail holds named contacts at real businesses, so this is a privacy setting
# as much as a housekeeping one: under the Privacy Act 2020 you shouldn't hold
# personal information longer than you need it. 12 months is a reasonable
# default if you follow up annually; drop it to 6 if you don't.
DETAIL_RETENTION_MONTHS = 12

# Memory versions are immutable and can only be redacted, never deleted, so the
# audit trail keeps the old content unless you clear it. `manage.py prune
# --redact-versions` scrubs versions belonging to pruned detail files.
REDACT_VERSIONS_ON_PRUNE = False

# --------------------------------------------------------------------------
# Your identity (goes into every draft — must be accurate)
# --------------------------------------------------------------------------
# The UEM Act 2007 requires a commercial message to identify the sender and
# give accurate information on how to readily contact them. It does not
# enumerate a phone number or postal address the way US CAN-SPAM does, so a
# working reply address plus company and website carries the obligation.
#
# The demo defaults below are a fictional studio on a reserved, non-routable
# domain. Override every one of them in .env before real outreach.
SENDER_NAME = os.environ.get("SENDER_NAME", "Alex Morgan")
SENDER_TITLE = os.environ.get("SENDER_TITLE", "Founder")
SENDER_COMPANY = os.environ.get("SENDER_COMPANY", "Tradie Web Co")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "hello@tradiewebco.example")
SENDER_WEBSITE = os.environ.get("SENDER_WEBSITE", "https://www.tradiewebco.example")
UNSUBSCRIBE_LINE = os.environ.get(
    "UNSUBSCRIBE_LINE",
    "If you'd rather not hear from me again, just reply with \"no thanks\" "
    "and I'll remove you from my list.",
)

# --------------------------------------------------------------------------
# Zoho MCP
# --------------------------------------------------------------------------
# From the Zoho MCP console (https://www.zoho.com/mcp/): create a server,
# add the Zoho Mail tools you want (draft/create-draft at minimum), authorise
# via OAuth, then copy the server URL here.
ZOHO_MCP_URL = os.environ.get("ZOHO_MCP_URL", "")
ZOHO_MCP_NAME = "zoho"

# Deny-by-default allowlist over the Zoho MCP server's tools.
#
# The server ships nine tools, including ZohoMail_sendEmail and
# ZohoMail_sendReplyEmail. It offers no dedicated draft tool: a draft is
# ZohoMail_sendEmail with "mode": "draft" in the body, so the sending tool
# cannot be removed without losing the ability to draft at all. Everything
# else is switched off here, which is the one guard that does not depend on
# the model behaving — the tools simply are not reachable.
#
# Verify against the live server after any change:  python zoho_oauth.py --verify
ZOHO_ALLOWED_TOOLS = [
    "ZohoMail_getMailAccounts",  # resolves the accountId the draft call needs
    "ZohoMail_sendEmail",        # drafting path; requires "mode": "draft"
]
# "url_embedded" if the console baked the key into the server URL itself;
# "static_bearer" if it gave you a long-lived token; "mcp_oauth" if you hold an
# access+refresh token pair and want Anthropic to auto-refresh.
#
# url_embedded is the weakest of the three: a vault substitutes secrets into
# request headers and bodies at egress, never into the URL, so a key carried in
# the URL cannot be vaulted. It is stored in the agent's mcp_servers entry and
# readable back from the Anthropic API, unlike a vaulted token. Prefer a
# header-auth mode if the Zoho console will issue one.
ZOHO_AUTH_MODE = os.environ.get("ZOHO_AUTH_MODE", "static_bearer")
ZOHO_ACCESS_TOKEN = os.environ.get("ZOHO_ACCESS_TOKEN", "")
ZOHO_REFRESH_TOKEN = os.environ.get("ZOHO_REFRESH_TOKEN", "")
ZOHO_CLIENT_ID = os.environ.get("ZOHO_CLIENT_ID", "")
ZOHO_CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET", "")
ZOHO_TOKEN_ENDPOINT = os.environ.get(
    "ZOHO_TOKEN_ENDPOINT", "https://accounts.zoho.com/oauth/v2/token"
)
ZOHO_TOKEN_EXPIRES_AT = os.environ.get("ZOHO_TOKEN_EXPIRES_AT", "")

# --------------------------------------------------------------------------
# Optional data sources
# --------------------------------------------------------------------------
# Free key from https://portal.api.business.govt.nz/ — gives you the official
# NZ business register (legal names, trading names, addresses, ANZSIC industry
# codes). Leave blank to run on web search alone.
NZBN_API_KEY = os.environ.get("NZBN_API_KEY", "")

# --------------------------------------------------------------------------
# Resource names + persisted IDs
# --------------------------------------------------------------------------
ENVIRONMENT_NAME = "trades-outreach-env"
AGENT_NAME = "Trades Website Outreach"
RESEARCHER_AGENT_NAME = "Trades Prospect Researcher"
MEMORY_STORE_NAME = "trades-outreach-memory"  # no spaces: it becomes a mount path
VAULT_NAME = "Trades Outreach Credentials"
DEPLOYMENT_NAME = "Trades daily outreach 9am NZT"

STATE_FILE = ROOT / "state.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def require(state: dict, key: str) -> str:
    value = state.get(key)
    if not value:
        raise SystemExit(
            f"Missing '{key}' in state.json. Run: python setup_agent.py"
        )
    return value
