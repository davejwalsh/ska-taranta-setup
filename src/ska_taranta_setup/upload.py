"""
Upload generated dashboards to a running Taranta.

Uses the same HTTP API as Taranta's own "import dashboard" button:

* ``POST <base>/auth/login`` (taranta-auth) returns a ``taranta_jwt`` cookie;
* ``GET <base>/dashboards/user/dashboards`` lists the user's dashboards;
* ``POST <base>/dashboards/`` creates a dashboard, or updates it if ``id`` is set.

With ``global.use_aws: true`` (the usual minikube setup) Taranta proxies
``/auth`` and ``/dashboards`` to the shared SKAO services, so uploads land in
that account's library there, not in the cluster. Log in with ``--token``
(your browser's ``taranta_jwt`` cookie) to upload to your own account.

``<base>`` is the Taranta URL without the trailing ``/taranta``, e.g.
``http://localhost:8080/ska-sat-lmc``. Dashboards are matched by name, so
re-uploading replaces the previous version instead of making copies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

#: The default user baked into the taranta-auth image's users.json.
DEFAULT_USER = "user1"
DEFAULT_PASSWORD = "abc123"


class UploadError(RuntimeError):
    """Taranta rejected a request."""


def api_base(url: str) -> str:
    """``http://host/ns/taranta/`` -> ``http://host/ns``."""
    url = url.rstrip("/")
    return url.removesuffix("/taranta")


@dataclass
class TarantaClient:
    """Minimal client for the Taranta dashboard API."""

    base: str
    session: requests.Session
    tango_db: str = "taranta"

    @classmethod
    def login(
        cls, url: str, username: str, password: str, tango_db: str = "taranta"
    ) -> TarantaClient:
        """Authenticate against taranta-auth."""
        base = api_base(url)
        session = requests.Session()
        resp = session.post(
            f"{base}/auth/login",
            json={"username": username, "password": password},
            timeout=30,
        )
        if resp.status_code != 200:
            raise UploadError(f"login failed ({resp.status_code}): {resp.text[:200]}")
        token = resp.json().get("taranta_jwt")
        if token:
            # The cookie is set Secure; set it explicitly so plain http works too.
            session.cookies.set("taranta_jwt", token)
        return cls(base=base, session=session, tango_db=tango_db)

    @classmethod
    def from_token(
        cls, url: str, token: str, tango_db: str = "taranta"
    ) -> TarantaClient:
        """Use an existing ``taranta_jwt`` (e.g. copied from a browser session)."""
        session = requests.Session()
        session.cookies.set("taranta_jwt", token)
        return cls(base=api_base(url), session=session, tango_db=tango_db)

    def whoami(self) -> str:
        """The user this client is authenticated as."""
        resp = self.session.get(f"{self.base}/auth/user", timeout=30)
        try:
            user = resp.json() if resp.status_code == 200 else None
        except ValueError:
            user = None
        if not user or not user.get("username"):
            raise UploadError("not logged in (token missing, invalid or expired)")
        return user["username"]

    def dashboard_url(self, dashboard_id: str) -> str:
        """Link that opens a dashboard in run mode."""
        return f"{self.base}/{self.tango_db}/dashboard?id={dashboard_id}&mode=run"

    def list_dashboards(self) -> dict[str, str]:
        """Map dashboard names to ids."""
        resp = self.session.get(
            f"{self.base}/dashboards/user/dashboards",
            params={"tangoDB": self.tango_db},
            timeout=30,
        )
        if resp.status_code != 200:
            raise UploadError(f"listing dashboards failed ({resp.status_code})")
        return {d["name"]: d.get("id") or d.get("_id") for d in resp.json()}

    def save(self, dashboard: dict[str, Any], dashboard_id: str = "") -> dict[str, Any]:
        """Create or update a dashboard from the contents of a ``.wj`` file."""
        body = {
            "id": dashboard_id,
            "name": dashboard["name"],
            "widgets": dashboard.get("widget") or dashboard.get("widgets", []),
            "variables": dashboard.get("variables", []),
            "environment": dashboard.get("environment", ["*/*"]),
            "filters": dashboard.get("filters", []),
            "tangoDB": self.tango_db,
        }
        resp = self.session.post(f"{self.base}/dashboards/", json=body, timeout=60)
        if resp.status_code != 200:
            raise UploadError(
                f"saving {dashboard['name']!r} failed "
                f"({resp.status_code}): {resp.text[:200]}"
            )
        try:
            result = resp.json()
        except ValueError as exc:
            # e.g. an HTML page from a proxy or the UI, not the dashboard API.
            raise UploadError(
                f"saving {dashboard['name']!r}: unexpected response from "
                f"{self.base}/dashboards/: {resp.text[:200]}"
            ) from exc
        if not result.get("id"):
            raise UploadError(f"saving {dashboard['name']!r}: no id returned: {result}")
        return result


@dataclass
class UploadResult:
    """The outcome for one file."""

    name: str
    file: str
    created: bool
    url: str


def _link_targets(widgets: list[dict[str, Any]]):
    """Yield the inputs of every DASHLINK widget, at any depth."""
    for widget in widgets:
        if widget.get("type") == "DASHLINK":
            yield widget["inputs"]
        yield from _link_targets(widget.get("innerWidgets", []))


def resolve_links(dashboard: dict[str, Any], ids: dict[str, str]) -> bool:
    """Fill dashboard ids into DASHLINK targets; return whether any changed."""
    changed = False
    for inputs in _link_targets(
        dashboard.get("widget") or dashboard.get("widgets", [])
    ):
        try:
            target = json.loads(inputs["DefaultDashboard"])
        except (KeyError, TypeError, ValueError):
            continue
        dashboard_id = ids.get(target.get("name", ""))
        if dashboard_id and target.get("id") != dashboard_id:
            target["id"] = dashboard_id
            inputs["DefaultDashboard"] = json.dumps(target)
            changed = True
    return changed


def upload_files(client: TarantaClient, files: list[Path]) -> list[UploadResult]:
    """
    Upload ``.wj`` files, updating dashboards that already have the same name.

    Then fill the saved dashboards' ids into the links between them (a second
    save of the dashboards with links), so links survive renames in Taranta.
    """
    existing = client.list_dashboards()
    results = []
    saved: list[tuple[dict[str, Any], str]] = []
    for path in files:
        dashboard = json.loads(path.read_text())
        dashboard_id = existing.get(dashboard["name"], "")
        result = client.save(dashboard, dashboard_id)
        existing[dashboard["name"]] = result["id"]
        saved.append((dashboard, result["id"]))
        results.append(
            UploadResult(
                name=dashboard["name"],
                file=path.name,
                created=bool(result.get("created")),
                url=client.dashboard_url(result["id"]),
            )
        )
    for dashboard, dashboard_id in saved:
        if resolve_links(dashboard, existing):
            client.save(dashboard, dashboard_id)
    return results
