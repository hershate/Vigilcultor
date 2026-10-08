"""Minimal GitHub REST client: App auth, installations, repos, issues.

Transport is injectable (``httpx.MockTransport`` in tests). Only the endpoints
needed by M2/M3 are covered; issue/PR write operations arrive with M4.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from vigilcultor.gh.appauth import create_app_jwt

API_BASE = "https://api.github.com"
PER_PAGE = 100


@dataclass
class RepoInfo:
    full_name: str
    default_branch: str
    private: bool = False
    fork: bool = False
    parent_full_name: str | None = None


@dataclass
class InstallationInfo:
    installation_id: int
    account_login: str
    account_type: str
    repository_selection: str
    repos: list[RepoInfo] = field(default_factory=list)


class GithubAppClient:
    """App-level API access (JWT): app metadata, installations, installation tokens."""

    def __init__(
        self,
        *,
        app_id: int,
        private_key_pem: str,
        transport: httpx.BaseTransport | None = None,
        base_url: str = API_BASE,
    ) -> None:
        self.app_id = app_id
        self._private_key_pem = private_key_pem
        self._client = httpx.Client(
            base_url=base_url, transport=transport, timeout=30, headers={"Accept": ACCEPT_V3}
        )

    def _app_headers(self) -> dict[str, str]:
        token = create_app_jwt(self._private_key_pem, self.app_id)
        return {"Authorization": f"Bearer {token}"}

    def get_app(self) -> dict:
        response = self._client.get("/app", headers=self._app_headers())
        response.raise_for_status()
        return response.json()

    def list_installations(self) -> list[InstallationInfo]:
        installations: list[InstallationInfo] = []
        page = 1
        while True:
            response = self._client.get(
                "/app/installations",
                params={"page": page, "per_page": PER_PAGE},
                headers=self._app_headers(),
            )
            response.raise_for_status()
            rows = response.json()
            if not rows:
                break
            installations.extend(
                InstallationInfo(
                    installation_id=row["id"],
                    account_login=row["account"]["login"],
                    account_type=row["account"]["type"],
                    repository_selection=row.get("repository_selection", "selected"),
                )
                for row in rows
            )
            if len(rows) < PER_PAGE:
                break
            page += 1
        return installations

    def installation_token(self, installation_id: int) -> tuple[str, dt.datetime]:
        """Create an installation access token; returns (token, expires_at)."""
        response = self._client.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers=self._app_headers(),
        )
        response.raise_for_status()
        data = response.json()
        expires_at = dt.datetime.strptime(data["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.UTC
        )
        return data["token"], expires_at


ACCEPT_V3 = "application/vnd.github+json"


class InstallationClient:
    """Installation-token API access: repository listing and issue polling."""

    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = API_BASE,
    ) -> None:
        self._token_provider = token_provider
        self._client = httpx.Client(
            base_url=base_url, transport=transport, timeout=30, headers={"Accept": ACCEPT_V3}
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_provider()}"}

    def get_repo(self, full_name: str) -> dict:
        response = self._client.get(f"/repos/{full_name}", headers=self._headers())
        response.raise_for_status()
        return response.json()

    def list_repos(self) -> list[RepoInfo]:
        repos: list[RepoInfo] = []
        page = 1
        while True:
            response = self._client.get(
                "/installation/repositories",
                params={"page": page, "per_page": PER_PAGE},
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json().get("repositories", [])
            if not rows:
                break
            repos.extend(_to_repo_info(row) for row in rows)
            if len(rows) < PER_PAGE:
                break
            page += 1
        return repos

    def get_permission(self, full_name: str, username: str) -> str:
        """Collaborator permission level: admin | write | read | none."""
        response = self._client.get(
            f"/repos/{full_name}/collaborators/{username}/permission",
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json().get("permission", "none")

    def list_issue_events(self, full_name: str, issue_number: int) -> list[dict]:
        """Issue timeline events (labeled/unlabeled/…), newest last."""
        response = self._client.get(
            f"/repos/{full_name}/issues/{issue_number}/events",
            params={"per_page": 100},
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    def list_issues(self, full_name: str, *, since: str | None = None) -> list[dict]:
        params: dict[str, str | int] = {"state": "all", "per_page": PER_PAGE}
        if since:
            params["sort"] = "updated"
            params["direction"] = "desc"
            params["since"] = since
        response = self._client.get(
            f"/repos/{full_name}/issues", params=params, headers=self._headers()
        )
        response.raise_for_status()
        rows = response.json()
        return [row for row in rows if "pull_request" not in row]


class CachedInstallationToken:
    """Thread-safe installation token with renewal margin."""

    _RENEW_MARGIN = dt.timedelta(minutes=5)

    def __init__(self, app_client: GithubAppClient, installation_id: int) -> None:
        self._app_client = app_client
        self._installation_id = installation_id
        self._token: str | None = None
        self._expires_at: dt.datetime | None = None
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            if self._token is None or (
                self._expires_at and self._expires_at - self._RENEW_MARGIN <= _now()
            ):
                self._token, self._expires_at = self._app_client.installation_token(
                    self._installation_id
                )
            return self._token


def _now() -> dt.datetime:
    return dt.datetime.fromtimestamp(time.time(), dt.UTC)


def _to_repo_info(row: dict) -> RepoInfo:
    parent = row.get("parent") or {}
    return RepoInfo(
        full_name=row["full_name"],
        default_branch=row.get("default_branch") or "main",
        private=row.get("private", False),
        fork=row.get("fork", False),
        parent_full_name=parent.get("full_name"),
    )
