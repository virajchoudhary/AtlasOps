"""Argo CD REST API tool wrappers."""

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import requests


class _ArgoCDConfigurationError(RuntimeError):
    """Raised when required Argo CD runtime configuration is unavailable."""


class _ArgoCDAuthenticationError(RuntimeError):
    """Raised when Argo CD authentication fails without exposing request data."""


@dataclass(frozen=True)
class _ArgoCDConfig:
    base_url: str
    username: str
    password: str = field(repr=False)
    verify_tls: bool


def _get_argocd_config() -> _ArgoCDConfig:
    base_url = os.getenv("ARGOCD_URL", "").strip()
    if not base_url:
        raise _ArgoCDConfigurationError(
            "argocd_configuration_error: missing required environment variable ARGOCD_URL"
        )
    try:
        parsed_url = urlsplit(base_url)
        valid_host = bool(parsed_url.hostname)
    except ValueError:
        valid_host = False
        parsed_url = None
    if (
        parsed_url is None
        or parsed_url.scheme.lower() not in {"http", "https"}
        or not valid_host
        or parsed_url.username is not None
        or parsed_url.password is not None
    ):
        raise _ArgoCDConfigurationError(
            "argocd_configuration_error: ARGOCD_URL must use http or https with a host"
        )
    username = os.getenv("ARGOCD_USER", "").strip()
    if not username:
        raise _ArgoCDConfigurationError(
            "argocd_configuration_error: missing required environment variable ARGOCD_USER"
        )
    password = os.getenv("ARGOCD_PASS", "")
    if not password.strip():
        raise _ArgoCDConfigurationError(
            "argocd_configuration_error: missing required environment variable ARGOCD_PASS"
        )
    verify_setting = os.getenv("ARGOCD_VERIFY_TLS", "true").strip().lower()
    if verify_setting in {"1", "true", "yes", "on"}:
        verify_tls = True
    elif verify_setting in {"0", "false", "no", "off"}:
        verify_tls = False
    else:
        raise _ArgoCDConfigurationError(
            "argocd_configuration_error: ARGOCD_VERIFY_TLS must be true or false"
        )
    return _ArgoCDConfig(
        base_url=base_url.rstrip("/"),
        username=username,
        password=password,
        verify_tls=verify_tls,
    )


_cached_token: str | None = None
_cached_token_identity: tuple[str, str, bytes] | None = None


def _get_token(config: _ArgoCDConfig) -> str:
    global _cached_token, _cached_token_identity
    identity = (
        config.base_url,
        config.username,
        hashlib.sha256(config.password.encode("utf-8")).digest(),
    )
    if _cached_token and _cached_token_identity == identity:
        return _cached_token
    try:
        response = requests.post(
            f"{config.base_url}/api/v1/session",
            json={"username": config.username, "password": config.password},
            timeout=10,
            verify=config.verify_tls,
        )
        response.raise_for_status()
        _cached_token = response.json()["token"]
        _cached_token_identity = identity
        return _cached_token
    except Exception:
        raise _ArgoCDAuthenticationError(
            "argocd_authentication_error: authentication request failed"
        ) from None


def _api(method: str, path: str, **kwargs) -> dict[str, Any]:
    try:
        config = _get_argocd_config()
        token = _get_token(config)
        response = requests.request(
            method,
            f"{config.base_url}/api/v1{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            verify=config.verify_tls,
            **kwargs,
        )
        if response.status_code == 401:
            global _cached_token, _cached_token_identity
            _cached_token = None
            _cached_token_identity = None
            token = _get_token(config)
            response = requests.request(
                method,
                f"{config.base_url}/api/v1{path}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
                verify=config.verify_tls,
                **kwargs,
            )
        response.raise_for_status()
        return {"success": True, "data": response.json()}
    except (_ArgoCDConfigurationError, _ArgoCDAuthenticationError) as exc:
        return {"success": False, "error": str(exc)}
    except Exception:
        return {"success": False, "error": "argocd_request_error: request failed"}


def argocd_list_apps() -> dict[str, Any]:
    """List all Argo CD applications."""
    result = _api("GET", "/applications")
    if result.get("success"):
        items = result["data"].get("items") or []
        result["apps"] = [
            {
                "name": app["metadata"]["name"],
                "health": app.get("status", {}).get("health", {}).get("status", "Unknown"),
                "sync": app.get("status", {}).get("sync", {}).get("status", "Unknown"),
                "revision": (app.get("status", {}).get("history") or [{}])[-1].get("id"),
            }
            for app in items
        ]
        result["count"] = len(items)
    return result


def argocd_app_history(app: str) -> dict[str, Any]:
    """Get deployment history for an Argo CD application."""
    result = _api("GET", f"/applications/{app}")
    if result.get("success"):
        history = result["data"].get("status", {}).get("history") or []
        result["history"] = [
            {"id": item.get("id"), "revision": item.get("revision"), "deployedAt": item.get("deployedAt")}
            for item in history[-10:]
        ]
    return result


def argocd_rollback(app: str, revision: str) -> dict[str, Any]:
    """Roll back an Argo CD application to a previous revision."""
    revision_id = int(revision) if str(revision).isdigit() else 0
    result = _api("POST", f"/applications/{app}/rollback", json={"id": revision_id})
    if result.get("success"):
        result["message"] = f"Rollback of {app} to revision {revision} initiated."
    return result


def argocd_app_get(app: str) -> dict[str, Any]:
    """Get details for a specific Argo CD application."""
    return _api("GET", f"/applications/{app}")
