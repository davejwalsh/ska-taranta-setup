"""Tests for uploading to Taranta, against a fake HTTP session."""

import json

from ska_taranta_setup.upload import TarantaClient, api_base, upload_files


class FakeResponse:
    """Just enough of requests.Response."""

    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    """Records calls; one dashboard named 'Old' already exists."""

    def __init__(self):
        self.posts = []
        self.cookies = {}

    def get(self, url, params=None, timeout=None):
        assert url.endswith("/dashboards/user/dashboards")
        return FakeResponse(200, [{"id": "abc", "name": "Old"}])

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return FakeResponse(200, {"id": json["id"] or "new", "created": not json["id"]})


def test_api_base():
    """The API lives next to /taranta, not under it."""
    assert api_base("http://h:8080/ns/taranta/") == "http://h:8080/ns"
    assert api_base("http://h/ns/taranta") == "http://h/ns"


def test_upload_creates_and_updates(tmp_path):
    """New names are created; existing names are updated in place."""
    for name in ("Old", "New"):
        (tmp_path / f"{name}.wj").write_text(
            json.dumps({"name": name, "widget": [{"id": "1"}]})
        )
    session = FakeSession()
    client = TarantaClient(base="http://h/ns", session=session, tango_db="taranta")
    lines = upload_files(client, sorted(tmp_path.glob("*.wj")))
    assert lines == ["created: New (New.wj)", "updated: Old (Old.wj)"]
    bodies = {body["name"]: body for _, body in session.posts}
    assert bodies["Old"]["id"] == "abc"
    assert bodies["New"]["widgets"] == [{"id": "1"}]
    assert bodies["New"]["tangoDB"] == "taranta"
