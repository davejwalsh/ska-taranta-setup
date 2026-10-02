"""Tests for uploading to Taranta, against a fake HTTP session."""

import json

import pytest

from ska_taranta_setup.upload import (
    TarantaClient,
    UploadError,
    api_base,
    upload_files,
)


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
        if url.endswith("/auth/user"):
            return FakeResponse(200, {"username": "user1", "groups": []})
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
    assert client.whoami() == "user1"
    results = upload_files(client, sorted(tmp_path.glob("*.wj")))
    assert [(r.name, r.created) for r in results] == [("New", True), ("Old", False)]
    assert results[1].url == "http://h/ns/taranta/dashboard?id=abc&mode=run"
    bodies = {body["name"]: body for _, body in session.posts}
    assert bodies["Old"]["id"] == "abc"
    assert bodies["New"]["widgets"] == [{"id": "1"}]
    assert bodies["New"]["tangoDB"] == "taranta"


def test_upload_fills_link_ids(tmp_path):
    """After saving, links between the uploaded dashboards get the real ids."""
    link = {
        "type": "DASHLINK",
        "inputs": {"DefaultDashboard": json.dumps({"name": "Old", "id": ""})},
    }
    (tmp_path / "a.wj").write_text(
        json.dumps({"name": "New", "widget": [{"type": "BOX", "innerWidgets": [link]}]})
    )
    session = FakeSession()
    client = TarantaClient(base="http://h/ns", session=session)
    upload_files(client, [tmp_path / "a.wj"])
    _, second = session.posts  # saved, then re-saved with the link resolved
    assert second[1]["id"] == "new"
    target = second[1]["widgets"][0]["innerWidgets"][0]["inputs"]["DefaultDashboard"]
    assert json.loads(target) == {"name": "Old", "id": "abc"}


def test_html_response_is_an_error():
    """A proxy or the UI answering instead of the API must not pass silently."""

    class HtmlSession(FakeSession):
        def post(self, url, json=None, timeout=None):
            response = FakeResponse(200, None)
            response.text = "<!doctype html>"

            def bad_json():
                raise ValueError("not json")

            response.json = bad_json
            return response

    client = TarantaClient(base="http://h/ns", session=HtmlSession())
    with pytest.raises(UploadError, match="unexpected response"):
        client.save({"name": "X", "widget": []})
