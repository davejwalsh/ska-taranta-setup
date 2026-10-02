"""
Upload generated dashboards to a running Taranta.

Uses the same HTTP API as Taranta's own "import dashboard" button:

* ``POST <base>/auth/login`` (taranta-auth) returns a ``taranta_jwt`` cookie;
* ``GET <base>/dashboards/user/dashboards`` lists the user's dashboards;
* ``POST <base>/dashboards/`` creates a dashboard, or updates it if ``id`` is set.

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
        return resp.json()


def upload_files(client: TarantaClient, files: list[Path]) -> list[str]:
    """Upload ``.wj`` files; return a line per file describing the outcome."""
    existing = client.list_dashboards()
    results = []
    for path in files:
        dashboard = json.loads(path.read_text())
        dashboard_id = existing.get(dashboard["name"], "")
        result = client.save(dashboard, dashboard_id)
        action = "created" if result.get("created") else "updated"
        results.append(f"{action}: {dashboard['name']} ({path.name})")
    return results
