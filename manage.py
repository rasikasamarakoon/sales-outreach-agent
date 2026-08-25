"""Day-to-day operations for the scheduled deployment and its memory store.

    python manage.py status              # schedule, next fire times, recent runs
    python manage.py runs [--failed]     # run history
    python manage.py pause               # stop scheduled firings (reversible)
    python manage.py unpause             # resume from the next occurrence
    python manage.py contacted [domain]  # who has already been emailed
    python manage.py forget <domain>     # allow re-contacting one business
    python manage.py memory              # store size, broken down by area
    python manage.py prune [--dry-run] [--months N] [--redact-versions]
"""

from __future__ import annotations

import re
import sys
from datetime import date

import anthropic

import config as cfg
from anthropic_compat import (
    list_deployment_runs,
    pause_deployment,
    unpause_deployment,
)

CONTACTED_INDEX = "/contacted/index/"
CONTACTED_DETAIL = "/contacted/detail/"
EXCLUDED_INDEX = "/excluded/index.md"
MONTH_IN_PATH = re.compile(r"/(\d{4})-(\d{2})/")


# ---------------------------------------------------------------------------
# Memory helpers
# ---------------------------------------------------------------------------

def list_memories(client, store_id: str, prefix: str, *, view: str = "basic") -> list:
    """Every memory under `prefix`, skipping directory-like prefix nodes."""
    entries = client.beta.memory_stores.memories.list(
        store_id, path_prefix=prefix, view=view
    )
    return [e for e in entries if getattr(e, "type", "memory") == "memory"]


def month_ordinal(year: int, month: int) -> int:
    return year * 12 + month


def month_from_path(path: str) -> tuple[int, int] | None:
    """Pull YYYY-MM out of `/contacted/detail/2026-08/acme-co-nz.md`."""
    match = MONTH_IN_PATH.search(path)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def human_bytes(count: float) -> str:
    for unit in ("B", "KB", "MB"):
        if count < 1024:
            return f"{count:.0f} B" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024
    return f"{count:.1f} GB"


def size_of(entries: list) -> int:
    return sum(getattr(e, "content_size_bytes", 0) or 0 for e in entries)


# ---------------------------------------------------------------------------
# Deployment commands
# ---------------------------------------------------------------------------

def cmd_status(client, state, _args) -> None:
    deployment_id = cfg.require(state, "deployment_id")
    print(f"Deployment  {deployment_id}")
    print(f"Schedule    {cfg.CRON_EXPRESSION}  {cfg.TIMEZONE}")
    print(f"Agent       {state.get('agent_id')}")
    print(f"Memory      {state.get('memory_store_id')}")
    print(f"Budget      ${int(cfg.DAILY_BUDGET_CENTS) / 100:.2f} per run\n")

    runs = list_deployment_runs(client, deployment_id, limit=5)
    if not runs:
        print("No runs yet.")
        return
    print("Recent runs:")
    for run in runs:
        error = run.get("error")
        outcome = f"FAILED {error['type']}" if error else run.get("session_id", "-")
        trigger = (run.get("trigger_context") or {}).get("type", "?")
        print(f"  {run.get('created_at')}  {trigger:9}  {outcome}")


def cmd_runs(client, state, args) -> None:
    failed_only = "--failed" in args
    runs = list_deployment_runs(
        client,
        cfg.require(state, "deployment_id"),
        limit=50,
        has_error=True if failed_only else None,
    )
    if not runs:
        print("No matching runs.")
        return
    for run in runs:
        error = run.get("error")
        if error:
            print(f"{run.get('created_at')}  FAILED  {error['type']}: {error.get('message')}")
        else:
            print(f"{run.get('created_at')}  ok      {run.get('session_id')}")


def cmd_pause(client, state, _args) -> None:
    pause_deployment(client, cfg.require(state, "deployment_id"))
    print("Paused. Scheduled runs are suppressed; `python run_now.py` still works.")


def cmd_unpause(client, state, _args) -> None:
    unpause_deployment(client, cfg.require(state, "deployment_id"))
    print("Unpaused. Resumes at the next occurrence — missed runs are not backfilled.")


# ---------------------------------------------------------------------------
# Memory commands
# ---------------------------------------------------------------------------

def cmd_contacted(client, state, args) -> None:
    """Read the monthly index files — the same cheap path the agent uses."""
    store_id = cfg.require(state, "memory_store_id")
    needle = args[0].lower() if args else None

    indexes = list_memories(client, store_id, CONTACTED_INDEX, view="full")
    if not indexes:
        print("Nobody contacted yet.")
        return

    total = 0
    for index in sorted(indexes, key=lambda m: m.path):
        lines = [ln for ln in (index.content or "").splitlines() if ln.strip()]
        total += len(lines)
        shown = [ln for ln in lines if needle in ln.lower()] if needle else lines
        if not shown:
            continue
        print(f"\n{index.path}  ({len(lines)} contacted)")
        for line in shown:
            print(f"  {line}")

    print(f"\n{total} business(es) contacted in total.")
    if needle and total:
        print(f"(filtered on {needle!r})")


def cmd_forget(client, state, args) -> None:
    """Drop a business from the index and delete its detail record."""
    if not args:
        raise SystemExit("Usage: python manage.py forget <domain>")
    domain = args[0].lower()
    store_id = cfg.require(state, "memory_store_id")
    removed = 0

    for index in list_memories(client, store_id, CONTACTED_INDEX, view="full"):
        lines = (index.content or "").splitlines()
        kept = [ln for ln in lines if domain not in ln.lower()]
        if len(kept) == len(lines):
            continue
        removed += len(lines) - len(kept)
        client.beta.memory_stores.memories.update(
            index.id,
            memory_store_id=store_id,
            content="\n".join(kept) + ("\n" if kept else ""),
        )
        print(f"  removed {len(lines) - len(kept)} line(s) from {index.path}")

    for detail in list_memories(client, store_id, CONTACTED_DETAIL):
        if domain in detail.path.lower():
            client.beta.memory_stores.memories.delete(
                detail.id, memory_store_id=store_id
            )
            print(f"  deleted {detail.path}")

    print(
        f"{domain} can be contacted again."
        if removed
        else f"No contacted record matching {domain!r}."
    )


def cmd_memory(client, state, _args) -> None:
    store_id = cfg.require(state, "memory_store_id")
    areas = {
        "contacted/index  (permanent)": CONTACTED_INDEX,
        "contacted/detail (expires)": CONTACTED_DETAIL,
        "excluded         (permanent)": "/excluded/",
        "niches": "/niches/",
        "playbook": "/playbook/",
    }
    print(f"Memory store {store_id}\n")
    grand_files = grand_bytes = 0
    for label, prefix in areas.items():
        entries = list_memories(client, store_id, prefix)
        used = size_of(entries)
        grand_files += len(entries)
        grand_bytes += used
        print(f"  {label:30} {len(entries):5} file(s)  {human_bytes(used):>10}")
    print(f"  {'TOTAL':30} {grand_files:5} file(s)  {human_bytes(grand_bytes):>10}")

    stale = [
        m
        for m in list_memories(client, store_id, CONTACTED_DETAIL)
        if is_expired(m.path, cfg.DETAIL_RETENTION_MONTHS)
    ]
    if stale:
        print(
            f"\n{len(stale)} detail file(s) are past the "
            f"{cfg.DETAIL_RETENTION_MONTHS}-month window. "
            "Run: python manage.py prune --dry-run"
        )


def is_expired(path: str, months: int) -> bool:
    parsed = month_from_path(path)
    if not parsed:
        return False  # unparseable month — leave it alone rather than guess
    today = date.today()
    cutoff = month_ordinal(today.year, today.month) - months
    return month_ordinal(*parsed) < cutoff


def cmd_prune(client, state, args) -> None:
    """Delete contacted/detail records older than the retention window.

    Deliberately dumb and deterministic: the month lives in the path, so no
    timestamp guessing and no model call. The index files are never touched —
    they are what stops a business being emailed twice.
    """
    dry_run = "--dry-run" in args
    redact = "--redact-versions" in args or cfg.REDACT_VERSIONS_ON_PRUNE
    months = cfg.DETAIL_RETENTION_MONTHS
    if "--months" in args:
        months = int(args[args.index("--months") + 1])

    store_id = cfg.require(state, "memory_store_id")
    details = list_memories(client, store_id, CONTACTED_DETAIL)
    expired = [m for m in details if is_expired(m.path, months)]

    print(
        f"{len(details)} detail file(s); {len(expired)} older than {months} month(s)."
    )
    if not expired:
        print("Nothing to prune.")
        return

    by_month: dict[str, int] = {}
    for memory in expired:
        parsed = month_from_path(memory.path)
        key = f"{parsed[0]}-{parsed[1]:02d}" if parsed else "?"
        by_month[key] = by_month.get(key, 0) + 1
    for month in sorted(by_month):
        print(f"  {month}: {by_month[month]} file(s)")

    if dry_run:
        print(f"\nDry run — nothing deleted. Would free {human_bytes(size_of(expired))}.")
        return

    freed = size_of(expired)
    for memory in expired:
        if redact:
            # Versions are immutable and can only be redacted, never deleted —
            # do it before the delete so the audit trail keeps who/when but
            # drops the contact details.
            for version in client.beta.memory_stores.memory_versions.list(
                store_id, memory_id=memory.id
            ):
                client.beta.memory_stores.memory_versions.redact(
                    version.id, memory_store_id=store_id
                )
        client.beta.memory_stores.memories.delete(memory.id, memory_store_id=store_id)

    print(
        f"\nDeleted {len(expired)} detail file(s), freed {human_bytes(freed)}."
        + (" Versions redacted." if redact else "")
        + "\nIndex files untouched — dedup history is intact."
    )


def cmd_vault(client, state, _args) -> None:
    """Show what the vault holds.

    Secret values are write-only: `token`, `access_token`, `refresh_token`,
    `client_secret` and `secret_value` are accepted on write and never
    returned by any read. That is the whole point of the vault, so this
    prints the metadata around the secrets, never the secrets.
    """
    vault_id = cfg.require(state, "vault_id")
    vault = client.beta.vaults.retrieve(vault_id)
    print(f"Vault  {vault.id}  {getattr(vault, 'display_name', '')}")

    creds = list(client.beta.vaults.credentials.list(vault_id))
    if not creds:
        print("\nNo credentials. Run: python setup_agent.py")
        return

    for cred in creds:
        auth = cred.auth
        kind = getattr(auth, "type", "?")
        print(f"\n  {cred.display_name}")
        print(f"    id           {cred.id}")
        print(f"    type         {kind}")

        if kind in ("mcp_oauth", "static_bearer"):
            print(f"    server url   {getattr(auth, 'mcp_server_url', '')}")
        if kind == "mcp_oauth":
            print(f"    expires at   {getattr(auth, 'expires_at', 'not stated')}")
            refresh = getattr(auth, "refresh", None)
            if refresh:
                print(f"    client id    {getattr(refresh, 'client_id', '')}")
                print(f"    token url    {getattr(refresh, 'token_endpoint', '')}")
                endpoint_auth = getattr(refresh, "token_endpoint_auth", None)
                print(f"    client auth  {getattr(endpoint_auth, 'type', '?')}")
                print("    refresh tok  stored, write-only (Anthropic refreshes for you)")
            else:
                print("    refresh      ABSENT — access will be lost when the token lapses")
        if kind == "environment_variable":
            print(f"    env var      {getattr(auth, 'secret_name', '')}")
            print("    value        stored, write-only")
            networking = getattr(auth, "networking", None)
            hosts = getattr(networking, "allowed_hosts", None)
            print(f"    hosts        {hosts if hosts else 'unrestricted'}")
            where = getattr(auth, "injection_location", None)
            if where:
                enabled = [k for k in ("header", "body") if getattr(where, k, False)]
                print(f"    injected in  {', '.join(enabled) or 'nothing'}")

    print(
        "\nSecret values are never returned by the API. The only readable copy is\n"
        "your local .env — treat that file as the source of truth."
    )


COMMANDS = {
    "status": cmd_status,
    "runs": cmd_runs,
    "vault": cmd_vault,
    "pause": cmd_pause,
    "unpause": cmd_unpause,
    "contacted": cmd_contacted,
    "forget": cmd_forget,
    "memory": cmd_memory,
    "prune": cmd_prune,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(__doc__)
        return 1
    COMMANDS[argv[1]](anthropic.Anthropic(), cfg.load_state(), argv[2:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
