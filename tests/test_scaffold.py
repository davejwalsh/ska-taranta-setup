"""Tests for ``ska-taranta init``."""

import tomllib

import pytest
import yaml

from ska_taranta_setup.config import load_config
from ska_taranta_setup.scaffold import init_project

CHART = """\
apiVersion: v2
name: my-proj
version: 0.1.0
dependencies:
  - name: ska-tango-devices
    version: 0.16.0
    repository: https://artefact.skao.int/repository/helm-internal
"""

VALUES = """\
# Default values
global:
  minikube: true
  tango_host: databaseds-tango-base:10000

image:
  name: my-proj  # keep this comment
"""

MAKEFILE = """\
include .make/base.mk
K8S_DEPLOY_WR_SIMULATOR ?= true

-include PrivateRules.mak
"""


@pytest.fixture
def project(tmp_path):
    """A minimal SKA-shaped project."""
    chart = tmp_path / "charts" / "my-proj"
    chart.mkdir(parents=True)
    (chart / "Chart.yaml").write_text(CHART)
    (chart / "values.yaml").write_text(VALUES)
    (tmp_path / "Makefile").write_text(MAKEFILE)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "my-proj"\n')
    (tmp_path / "uv.lock").write_text("")
    return tmp_path


def test_init(project):
    """Charts, values, Makefile and config are all wired up."""
    init_project(load_config(project), helm_update=False)

    chart = yaml.safe_load((project / "charts/my-proj/Chart.yaml").read_text())
    names = [d.get("alias") or d["name"] for d in chart["dependencies"]]
    assert names == [
        "ska-tango-devices",
        "ska-tango-taranta",
        "ska-tango-taranta-auth",
        "tangogql",
    ]

    values_text = (project / "charts/my-proj/values.yaml").read_text()
    assert "# keep this comment" in values_text
    values = yaml.safe_load(values_text)
    assert values["global"]["tango_host"] == "databaseds-tango-base:10000"  # untouched
    assert values["ska-tango-taranta"]["enabled"] is True
    assert values["tangogql"]["ska-tango-base"]["enabled"] is False

    makefile = (project / "Makefile").read_text()
    assert makefile.index("-include taranta.mk") < makefile.index(
        "-include PrivateRules.mak"
    )
    mk = (project / "taranta.mk").read_text()
    assert "TARANTA_RUN ?= uv run" in mk
    assert "k8s-pre-install-chart: taranta-auth-secret" in mk

    config = tomllib.loads((project / "pyproject.toml").read_text())["tool"][
        "ska-taranta-setup"
    ]
    assert config["chart"] == "charts/my-proj"
    assert config["helmfile_env"] == {"K8S_DEPLOY_WR_SIMULATOR": "true"}
    assert (project / "dashboards").is_dir()


def test_init_is_idempotent(project):
    """A second run changes nothing."""
    init_project(load_config(project), helm_update=False)
    before = {p: p.read_text() for p in project.rglob("*") if p.is_file()}
    report = init_project(load_config(project), helm_update=False)
    assert report.changes == []
    assert {p: p.read_text() for p in project.rglob("*") if p.is_file()} == before


def test_existing_values_not_overwritten(project):
    """Values the user already set win."""
    values = project / "charts/my-proj/values.yaml"
    values.write_text(VALUES + "ska-tango-taranta:\n  enabled: false\n")
    init_project(load_config(project), helm_update=False)
    data = yaml.safe_load(values.read_text())
    assert data["ska-tango-taranta"]["enabled"] is False
    assert data["ska-tango-taranta"]["TANGO_DBS"] == []


def test_dry_run_changes_nothing(project):
    """--dry-run reports but doesn't write."""
    before = {p: p.read_text() for p in project.rglob("*") if p.is_file()}
    report = init_project(load_config(project), dry_run=True)
    assert report.changes
    assert {p: p.read_text() for p in project.rglob("*") if p.is_file()} == before


def test_strict_schema_is_extended(project):
    """A values schema with additionalProperties: false allows the new keys."""
    schema = project / "charts/my-proj/values.schema.json"
    schema.write_text('{"additionalProperties": false, "properties": {"global": {}}}')
    init_project(load_config(project), helm_update=False)
    assert '"ska-tango-taranta"' in schema.read_text()
