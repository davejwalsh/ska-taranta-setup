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


def _linking(name, target):
    link = {
        "type": "DASHLINK",
        "inputs": {"DefaultDashboard": json.dumps({"name": target, "id": ""})},
    }
    return {"name": name, "widget": [{"type": "BOX", "innerWidgets": [link]}]}


def _target(body):
    raw = body["widgets"][0]["innerWidgets"][0]["inputs"]["DefaultDashboard"]
    return json.loads(raw)


def test_links_to_existing_dashboards_need_one_save(tmp_path):
    """A link to a dashboard already in the library is resolved before saving."""
    (tmp_path / "a.wj").write_text(json.dumps(_linking("New", "Old")))
    session = FakeSession()
    upload_files(
        TarantaClient(base="http://h/ns", session=session), [tmp_path / "a.wj"]
    )
    ((_, body),) = session.posts  # saved once, link already filled in
    assert _target(body) == {"name": "Old", "id": "abc"}


def test_links_to_new_dashboards_are_filled_after(tmp_path):
    """A link to a dashboard created in the same upload gets a second save."""
    (tmp_path / "1.wj").write_text(json.dumps(_linking("A", "B")))
    (tmp_path / "2.wj").write_text(json.dumps({"name": "B", "widget": []}))
    session = FakeSession()
    seen = []
    upload_files(
        TarantaClient(base="http://h/ns", session=session),
        [tmp_path / "1.wj", tmp_path / "2.wj"],
        progress=lambda r: seen.append(r.name),
    )
    assert seen == ["A", "B"]
    assert [body["name"] for _, body in session.posts] == ["A", "B", "A"]
    assert _target(session.posts[-1][1])["id"] == "new"


def test_gateway_errors_are_retried(monkeypatch):
    """A transient 502 from the shared service doesn't fail the upload."""
    monkeypatch.setattr("ska_taranta_setup.upload.RETRY_DELAY_S", 0)

    class FlakySession(FakeSession):
        calls = 0

        def post(self, url, json=None, timeout=None):
            FlakySession.calls += 1
            if FlakySession.calls == 1:
                return FakeResponse(502, {"error": "bad gateway"})
            return super().post(url, json=json, timeout=timeout)

    client = TarantaClient(base="http://h/ns", session=FlakySession())
    assert client.save({"name": "X", "widget": []})["id"] == "new"
    assert FlakySession.calls == 2


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
