"""Tests for the HDB++ archive scan and its Grafana dashboard."""

import json

import pytest
import yaml

from ska_taranta_setup.archive import (
    ArchivedAttribute,
    ArchiveDB,
    ArchiveSnapshot,
    Psql,
    find_archive_db,
    find_archiving_configs,
    kube_namespace,
    scan_configs,
    scan_database,
)
from ska_taranta_setup.config import load_config
from ska_taranta_setup.grafana import build_dashboard, value_mappings
from ska_taranta_setup.model import (
    AttributeInfo,
    DeviceInstance,
    DeviceInterface,
    Snapshot,
)
from ska_taranta_setup.options import ArchiverOptions, OptionsError

CONNECT = (
    "connect_string=user=postgres password=tango host=archive-db port=5432 dbname=hdb"
)
DEV = "low-infra/bms/rpf-s8"


def _attr(name, table, **kwargs):
    return ArchivedAttribute(device=DEV, name=name.lower(), table=table, **kwargs)


@pytest.fixture
def archive():
    """What a BMS facility's archive holds."""
    return ArchiveSnapshot(
        [
            _attr("State", "att_scalar_devstate", label="State"),
            _attr(
                "healthState",
                "att_scalar_devenum",
                label="healthState",
                enum_labels=["OK", "DEGRADED", "FAILED", "UNKNOWN"],
            ),
            _attr("chiller_fault", "att_scalar_devboolean"),
            _attr("chw_supply_temp", "att_scalar_devdouble", unit="degC"),
            _attr("chw_return_temp", "att_scalar_devdouble", unit="degC"),
            _attr("chiller_mode", "att_scalar_devlong64"),
            _attr("activeAlarms", "att_array_devstring", label="activeAlarms"),
        ],
        source="test",
    )


def _panels(panels):
    for panel in panels:
        yield panel
        yield from _panels(panel.get("panels", []))


def test_types_from_table_names():
    """The table name gives the Tango type and format."""
    attr = _attr("x", "att_array_devdouble")
    assert (attr.dtype, attr.dformat, attr.is_numeric) == (
        "DevDouble",
        "SPECTRUM",
        False,
    )
    state = _attr("state", "att_scalar_devstate", label="State")
    assert (state.dtype, state.is_discrete, state.display) == (
        "DevState",
        True,
        "State",
    )


def test_connect_string():
    """The event subscriber's libpq settings give the database."""
    db = ArchiveDB.from_connect_string(CONNECT.split("=", 1)[1])
    assert (db.host, db.port, db.dbname, db.user, db.password) == (
        "archive-db",
        5432,
        "hdb",
        "postgres",
        "tango",
    )
    assert db.dsn == "postgresql://postgres:tango@archive-db:5432/hdb"


def test_find_archive_db_from_chart_devices(tmp_path):
    """An HdbEventSubscriber's LibConfiguration, as rendered from the chart."""
    es = DeviceInstance(
        "hdb/es/1",
        "HdbEventSubscriber",
        properties={"LibConfiguration": ["libname=libhdb++timescale.so", CONNECT]},
    )
    assert find_archive_db(tmp_path, [es]).host == "archive-db"


def test_find_archive_db_from_files(tmp_path):
    """Otherwise any dsconfig file in the project; templated ones are skipped."""
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "tango.json").write_text(
        json.dumps({"LibConfiguration": ["libname=libhdb++timescale.so", CONNECT]})
    )
    assert find_archive_db(tmp_path).password == "tango"
    assert find_archive_db(tmp_path / "deploy" / "nothing-here") is None


def test_scan_database():
    """Rows from att_conf/att_parameter become attributes; enum labels split."""

    class FakePsql(Psql):
        def query(self, sql):
            assert "att_conf" in sql
            enum = "att_scalar_devenum"
            return [
                [DEV, "healthstate", enum, "healthState", "", "OK\x1eFAILED"],
                [DEV, "temp", "att_scalar_devdouble", "temp", "degC", ""],
            ]

    snap = scan_database(FakePsql([], "fake"))
    assert snap.source == "fake"
    assert snap.attributes[0].enum_labels == ["OK", "FAILED"]
    assert snap.attributes[1].unit == "degC"


def test_offline_scan_from_archiving_config(tmp_path):
    """yaml2archiving files, typed from the devices' interfaces."""
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "archive.yaml").write_text(
        yaml.safe_dump(
            {
                "db": "{{ .Values.global.tango_host }}",
                "manager": "hdb/cm/1",
                "archiver": "hdb/es/1",
                "configuration": [
                    {"class": "Bms", "attributes": {"state": {}, "temp": {}}}
                ],
            }
        )
    )
    (tmp_path / "deploy" / "other.yaml").write_text("configuration: []\n")
    paths = find_archiving_configs(tmp_path)
    assert [p.name for p in paths] == ["archive.yaml"]
    interface = DeviceInterface(
        "Bms",
        attributes=[
            AttributeInfo("State", dtype="DevState"),
            AttributeInfo("temp", dtype="DevDouble", unit="degC"),
        ],
    )
    snapshot = Snapshot(
        devices=[DeviceInstance(DEV, "Bms", interface="i")], interfaces={"i": interface}
    )
    archived = scan_configs(paths, snapshot, tmp_path).attributes
    assert [(a.name, a.table, a.unit) for a in archived] == [
        ("state", "att_scalar_devstate", ""),
        ("temp", "att_scalar_devdouble", "degC"),
    ]


def test_snapshot_round_trip(tmp_path, archive):
    """The scan is cached as JSON."""
    archive.save(tmp_path / "a.json")
    assert ArchiveSnapshot.load(tmp_path / "a.json") == archive


def test_dashboard_layout(archive):
    """Unique ids, panels on the grid, devices in rows, every query filtered."""
    dashboard = build_dashboard(archive, "BMS archive", ArchiverOptions())
    assert dashboard["uid"] == "bms-archive"
    panels = list(_panels(dashboard["panels"]))
    ids = [p["id"] for p in panels]
    assert len(ids) == len(set(ids))
    for panel in panels:
        pos = panel["gridPos"]
        assert pos["x"] + pos["w"] <= 24
        for target in panel.get("targets", []):
            assert target["datasource"]["uid"] == "${ds}"
    rows = [p["title"] for p in dashboard["panels"] if p["type"] == "row"]
    assert rows == ["Explore", "Archiver", DEV]
    titles = {p["title"]: p for p in panels}
    # Both temperatures share one plot (same unit), in Grafana's unit.
    temperature = titles["Temperature [degC]"]
    assert temperature["fieldConfig"]["defaults"]["unit"] == "celsius"
    assert "'chw_supply_temp', 'chw_return_temp'" in temperature["targets"][0]["rawSql"]
    # Event data: values are held from before the range and up to its end.
    sql = temperature["targets"][0]["rawSql"]
    assert "$__timeFilter(d.data_time)" in sql
    assert "d.data_time < $__timeFrom()" in sql
    assert "Text and array events (newest first)" in titles
    names = [v["name"] for v in dashboard["templating"]["list"]]
    assert names == ["ds", "device", "attribute"]


def test_state_mappings(archive):
    """States, enums and booleans are named and coloured, not "< 80"."""
    by_name = {a.name: a for a in archive.attributes}
    state = value_mappings(by_name["state"])[0]["options"]
    assert (state["0"]["text"], state["8"]["text"]) == ("ON", "FAULT")
    health = value_mappings(by_name["healthstate"])[0]["options"]
    assert (health["0"]["color"], health["2"]["color"]) == ("green", "red")
    fault = value_mappings(by_name["chiller_fault"])[0]["options"]
    assert fault["1"]["color"] == "red"  # a fault being true is bad
    dashboard = build_dashboard(archive, "t", ArchiverOptions())
    states = next(p for p in _panels(dashboard["panels"]) if p["title"] == "States")
    assert "color" not in states["fieldConfig"]["defaults"]
    assert {o["matcher"]["options"] for o in states["fieldConfig"]["overrides"]} == {
        "State",
        "healthState",
        "chiller_fault",
    }


def test_dashboard_options(archive):
    """Exclusions, per-attribute plots and the time range are configurable."""
    opts = ArchiverOptions(
        exclude_attributes=["chw_return.*"], group_by="attribute", time_from="now-7d"
    )
    dashboard = build_dashboard(archive, "t", opts)
    text = json.dumps(dashboard)
    assert "chw_return_temp" not in text
    assert dashboard["time"]["from"] == "now-7d"
    titles = [p["title"] for p in _panels(dashboard["panels"])]
    assert "Chw Supply Temp [degC]" in titles
    with pytest.raises(OptionsError):
        ArchiverOptions(group_by="colour")


def test_archiver_options_from_pyproject(tmp_path):
    """[tool.ska-taranta-setup.archiver] is read, and typos are rejected."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\n[tool.ska-taranta-setup.archiver]\ncolumns = 3\n'
    )
    assert load_config(tmp_path).archiver.columns == 3
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\n[tool.ska-taranta-setup.archiver]\ncolums = 3\n'
    )
    with pytest.raises(OptionsError, match="did you mean 'columns'"):
        load_config(tmp_path)


def test_kube_namespace(tmp_path, monkeypatch):
    """From the environment, else the Makefile, else the default."""
    monkeypatch.delenv("KUBE_NAMESPACE", raising=False)
    assert kube_namespace(tmp_path, "proj") == "proj"
    (tmp_path / "Makefile").write_text("KUBE_NAMESPACE ?= ska-low-bms\n")
    assert kube_namespace(tmp_path, "proj") == "ska-low-bms"
    monkeypatch.setenv("KUBE_NAMESPACE", "other")
    assert kube_namespace(tmp_path, "proj") == "other"


def test_grafana_template_and_values(tmp_path):
    """The chart ships the dashboards and, once on, the archive data source."""
    import subprocess

    from ska_taranta_setup.grafana_deploy import datasource_config
    from ska_taranta_setup.scaffold import grafana_values

    values = grafana_values()["grafana"]
    assert values["enabled"] is False
    assert values["sidecar"]["datasources"]["label"] == "grafana_datasource"
    source = datasource_config(ArchiveDB.from_connect_string(CONNECT.split("=", 1)[1]))
    entry = source["datasources"][0]
    assert (entry["url"], entry["jsonData"]["database"]) == ("archive-db:5432", "hdb")
    assert entry["secureJsonData"]["password"] == "tango"

    import shutil

    if shutil.which("helm") is None:
        pytest.skip("helm not installed")
    from importlib import resources

    chart = tmp_path / "c"
    (chart / "templates").mkdir(parents=True)
    (chart / "grafana").mkdir()
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: c\nversion: 0.1.0\n")
    (chart / "grafana" / "d.json").write_text('{"title": "d"}')
    template = resources.files("ska_taranta_setup.templates") / "grafana.yaml"
    (chart / "templates" / "grafana.yaml").write_text(template.read_text())
    (tmp_path / "ds.yaml").write_text(yaml.safe_dump(source))

    def render(*args):
        out = subprocess.run(
            ["helm", "template", "r", str(chart), *args],
            capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip
        return [d for d in yaml.safe_load_all(out) if d]

    assert render() == []  # off by default: nothing at all
    on = ["--set", "grafana.enabled=true"]
    docs = {
        d["kind"]: d
        for d in render(
            *on, "--set-file", f"grafana.archiveDatasource={tmp_path / 'ds.yaml'}"
        )
    }
    assert docs["ConfigMap"]["metadata"]["labels"] == {"grafana_dashboard": "1"}
    assert json.loads(docs["ConfigMap"]["data"]["d.json"]) == {"title": "d"}
    secret = docs["Secret"]
    assert secret["metadata"]["labels"] == {"grafana_datasource": "1"}
    assert yaml.safe_load(secret["stringData"]["hdbpp.yaml"]) == source
    assert {d["kind"] for d in render(*on)} == {"ConfigMap"}  # no source given


def test_grafana_needs_a_deployed_archive(tmp_path, monkeypatch):
    """`grafana` refuses, and starts nothing, unless HDB++ is running."""
    from click.testing import CliRunner

    from ska_taranta_setup import cli

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "p"\n')
    monkeypatch.setattr(
        "ska_taranta_setup.instances.discover_instances", lambda config: ([], "none")
    )
    result = CliRunner().invoke(cli.main, ["-C", str(tmp_path), "grafana"])
    assert result.exit_code == 1
    assert "doesn't configure HDB++" in result.output

    (tmp_path / "tango.json").write_text(json.dumps({"x": ["libhdb++", CONNECT]}))
    monkeypatch.setattr("ska_taranta_setup.archive.psql_in_pod", lambda db, ns: None)
    monkeypatch.setattr("ska_taranta_setup.archive.psql_in_container", lambda db: None)
    result = CliRunner().invoke(cli.main, ["-C", str(tmp_path), "grafana"])
    assert result.exit_code == 1
    assert "HDB++ isn't running" in result.output
