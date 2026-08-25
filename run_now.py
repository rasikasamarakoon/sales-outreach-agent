"""Trigger the daily run immediately and watch it, instead of waiting for 9am.

Fires a manual deployment run (works even while the deployment is paused),
streams the session's events to your terminal, then downloads whatever the
agent wrote to /mnt/session/outputs/ into ./output/.

    python run_now.py                 # trigger a new run and follow it
    python run_now.py sesn_01ABC...   # re-attach to an existing session
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import anthropic

import config as cfg
from anthropic_compat import get_deployment_run, run_deployment

OUTPUT_DIR = Path(__file__).parent / "output"


def trigger(client, deployment_id: str) -> str:
    run = run_deployment(client, deployment_id)
    run_id = run["id"]
    print(f"Deployment run {run_id} started.")

    # The session is created a moment after the run record.
    for _ in range(30):
        if run.get("session_id"):
            return run["session_id"]
        if run.get("error"):
            error = run["error"]
            raise SystemExit(
                f"Run failed before creating a session: "
                f"{error.get('type')} — {error.get('message')}"
            )
        time.sleep(2)
        run = get_deployment_run(client, run_id)

    raise SystemExit(f"Run {run_id} produced no session after 60s. Check the console.")


def render_blocks(blocks) -> None:
    for block in blocks or []:
        if getattr(block, "type", None) == "text":
            print(block.text, end="", flush=True)


def follow(client, session_id: str) -> None:
    workspace = cfg.load_state().get("workspace_id", "default")
    print(
        f"\nSession {session_id}\n"
        f"Trace: https://platform.claude.com/workspaces/{workspace}/sessions/{session_id}\n"
        + "-" * 72
    )

    with client.beta.sessions.events.stream(session_id=session_id) as stream:
        for event in stream:
            kind = event.type

            if kind == "agent.message":
                render_blocks(event.content)
            elif kind == "agent.tool_use":
                print(f"\n  [tool] {event.name}", flush=True)
            elif kind == "agent.mcp_tool_use":
                print(f"\n  [zoho] {event.name}", flush=True)
            elif kind == "session.thread_created":
                print(f"\n  [thread] spawned {getattr(event, 'agent_name', '?')}", flush=True)
            elif kind == "agent.thread_message_received":
                print(
                    f"\n  [thread] report from "
                    f"{getattr(event, 'from_agent_name', '?')}",
                    flush=True,
                )
            elif kind == "session.error":
                print(f"\n  [error] {getattr(event, 'error', None)}", flush=True)

            elif kind == "session.status_terminated":
                print("\n" + "-" * 72 + "\nSession terminated.")
                break
            elif kind == "session.status_idle":
                reason = getattr(event.stop_reason, "type", None)
                if reason == "requires_action":
                    # Waiting on a tool confirmation or custom tool result. The
                    # run has stalled, not finished: nothing here answers it,
                    # and the stream usually closes shortly after, so say so
                    # rather than letting the loop fall out silently.
                    blocked = getattr(event.stop_reason, "event_ids", []) or []
                    print(
                        "\n" + "-" * 72
                        + "\nSession is BLOCKED waiting for client-side approval"
                        + (f" on {', '.join(blocked)}" if blocked else "")
                        + "\nA scheduled run has no client to answer this. Set the"
                        + "\ntool's permission_policy to always_allow in"
                        + "\nsetup_agent.py, re-run it, then trigger a fresh run.",
                        flush=True,
                    )
                    return
                print("\n" + "-" * 72 + f"\nSession idle ({reason}).")
                if reason == "budget_reached":
                    print(
                        "Hit the per-run spend cap. Raise DAILY_BUDGET_CENTS in "
                        "config.py and re-run setup_agent.py, or update this "
                        "session's budget to resume it."
                    )
                break


def download_outputs(client, session_id: str) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    files = []
    for attempt in range(4):  # brief indexing lag after the session goes idle
        listing = client.beta.files.list(
            scope_id=session_id, betas=["managed-agents-2026-04-01"]
        )
        files = list(listing.data)
        if files:
            break
        time.sleep(2 * (attempt + 1))

    if not files:
        print("\nNo output files found for this session.")
        return

    print(f"\nDownloading {len(files)} file(s) to {OUTPUT_DIR}:")
    for meta in files:
        safe_name = Path(meta.filename).name
        if not safe_name or safe_name in (".", ".."):
            print(f"  skipped odd filename: {meta.filename!r}")
            continue
        client.beta.files.download(meta.id).write_to_file(OUTPUT_DIR / safe_name)
        print(f"  {safe_name}  ({meta.size_bytes} bytes)")


def main(argv: list[str]) -> int:
    client = anthropic.Anthropic()
    state = cfg.load_state()

    if len(argv) > 1:
        session_id = argv[1]
    else:
        session_id = trigger(client, cfg.require(state, "deployment_id"))

    follow(client, session_id)
    download_outputs(client, session_id)

    print(
        "\nDrafts are in Zoho Mail — review them there before sending. "
        f"The prospect sheet and full draft text are in {OUTPUT_DIR}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
