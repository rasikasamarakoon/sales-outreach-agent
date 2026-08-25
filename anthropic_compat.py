"""Deployment helpers with a raw-HTTP fallback.

Scheduled deployments are a newer part of the Managed Agents surface than
agents/sessions/environments, so `client.beta.deployments` may not exist in the
installed SDK yet. Everything here tries the SDK first and falls back to raw
HTTP against the same endpoints, and always returns plain dicts so callers
don't have to care which path ran.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

API_BASE = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
BETA_HEADER = "managed-agents-2026-04-01"


def _as_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    for attr in ("to_dict", "model_dump"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            return fn()
    return dict(obj)  # last resort


def _headers() -> dict[str, str]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set, and this SDK build needs the raw-HTTP "
            "fallback for deployments. Set the key, or upgrade the anthropic "
            "package until `client.beta.deployments` exists."
        )
    return {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": BETA_HEADER,
        "content-type": "application/json",
    }


def _request(method: str, path: str, *, json: dict | None = None,
             params: dict | None = None) -> dict:
    with httpx.Client(timeout=60.0) as http:
        response = http.request(
            method, f"{API_BASE}{path}", headers=_headers(), json=json, params=params
        )
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text}")
    return response.json() if response.content else {}


def _sdk(client, name: str):
    """Return client.beta.<name> if this SDK build has it, else None."""
    return getattr(client.beta, name, None)


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------

def create_deployment(client, **body) -> dict:
    api = _sdk(client, "deployments")
    if api is not None:
        return _as_dict(api.create(**body))
    return _request("POST", "/v1/deployments", json=body)


def update_deployment(client, deployment_id: str, **body) -> dict:
    api = _sdk(client, "deployments")
    if api is not None:
        return _as_dict(api.update(deployment_id, **body))
    return _request("POST", f"/v1/deployments/{deployment_id}", json=body)


def run_deployment(client, deployment_id: str) -> dict:
    """Fire a manual run immediately. Works even while the deployment is paused."""
    api = _sdk(client, "deployments")
    if api is not None:
        return _as_dict(api.run(deployment_id))
    return _request("POST", f"/v1/deployments/{deployment_id}/run")


def pause_deployment(client, deployment_id: str) -> dict:
    api = _sdk(client, "deployments")
    if api is not None:
        return _as_dict(api.pause(deployment_id))
    return _request("POST", f"/v1/deployments/{deployment_id}/pause")


def unpause_deployment(client, deployment_id: str) -> dict:
    api = _sdk(client, "deployments")
    if api is not None:
        return _as_dict(api.unpause(deployment_id))
    return _request("POST", f"/v1/deployments/{deployment_id}/unpause")


def archive_deployment(client, deployment_id: str) -> dict:
    """Terminal — the schedule stops and the deployment becomes immutable."""
    api = _sdk(client, "deployments")
    if api is not None:
        return _as_dict(api.archive(deployment_id))
    return _request("POST", f"/v1/deployments/{deployment_id}/archive")


# ---------------------------------------------------------------------------
# Deployment runs
# ---------------------------------------------------------------------------

def list_deployment_runs(client, deployment_id: str, *, limit: int = 20,
                         has_error: bool | None = None) -> list[dict]:
    api = _sdk(client, "deployment_runs")
    if api is not None:
        kwargs: dict[str, Any] = {"deployment_id": deployment_id, "limit": limit}
        if has_error is not None:
            kwargs["has_error"] = has_error
        page = api.list(**kwargs)
        return [_as_dict(run) for run in getattr(page, "data", page)]
    params: dict[str, Any] = {"deployment_id": deployment_id, "limit": limit}
    if has_error is not None:
        params["has_error"] = "true" if has_error else "false"
    return _request("GET", "/v1/deployment_runs", params=params).get("data", [])


def get_deployment_run(client, run_id: str) -> dict:
    api = _sdk(client, "deployment_runs")
    if api is not None:
        return _as_dict(api.retrieve(run_id))
    return _request("GET", f"/v1/deployment_runs/{run_id}")
