"""
``ska-taranta grafana``: run Grafana for an HDB++ archive that's deployed.

Grafana is off by default. When the archive database is running, this turns
it on next to it, with the archive as its data source and the generated
dashboard:

* in Kubernetes, by upgrading the project's release with
  ``grafana.enabled=true`` (the subchart ``init`` added), keeping every other
  value it was deployed with;
* with Docker Compose, by running a Grafana container on the stack's network.

The database password never lands in the project: it's handed to Helm (or
the container) from a temporary file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
import yaml

from ska_taranta_setup.archive import ArchiveDB


class GrafanaDeployError(RuntimeError):
    """Grafana couldn't be started or stopped."""


def datasource_config(db: ArchiveDB) -> dict[str, Any]:
    """Grafana provisioning for the archive database."""
    return {
        "apiVersion": 1,
        "datasources": [
            {
                "name": "HDB++",
                "uid": "hdbpp",
                "type": "grafana-postgresql-datasource",
                "url": f"{db.host}:{db.port}",
                "user": db.user,
                "isDefault": True,
                "jsonData": {
                    "database": db.dbname,
                    "sslmode": "disable",
                    "timescaledb": True,
                    "postgresVersion": 1500,
                },
                "secureJsonData": {"password": db.password},
            }
        ],
    }


def _run(cmd: list[str], what: str, timeout: float | None = None) -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise GrafanaDeployError(f"{what}: timed out") from exc
    if proc.returncode != 0:
        raise GrafanaDeployError(f"{what} failed:\n{proc.stderr.strip()[-2000:]}")
    return proc.stdout


# --------------------------------------------------------------------------
# Kubernetes (helm)
# --------------------------------------------------------------------------


def chart_has_grafana(chart: Path) -> bool:
    """Whether ``init`` added the Grafana subchart."""
    data = yaml.safe_load((chart / "Chart.yaml").read_text()) or {}
    return any(d.get("name") == "grafana" for d in data.get("dependencies") or [])


def find_release(chart: Path, namespace: str) -> str | None:
    """The release of ``chart`` deployed in ``namespace``."""
    name = yaml.safe_load((chart / "Chart.yaml").read_text())["name"]
    out = _run(["helm", "list", "-n", namespace, "-o", "json"], "helm list")
    for release in json.loads(out or "[]"):
        if release["chart"].rsplit("-", 1)[0] == name:
            return release["name"]
    return None


def _ensure_dependencies(chart: Path) -> None:
    if any((chart / "charts").glob("grafana-*.tgz")):
        return
    try:
        _run(["helm", "dependency", "build", str(chart)], "helm dependency build")
    except GrafanaDeployError:
        # Chart.lock out of step with Chart.yaml: refresh it.
        _run(["helm", "dependency", "update", str(chart)], "helm dependency update")


def set_enabled(
    chart: Path,
    release: str,
    namespace: str,
    enabled: bool,
    db: ArchiveDB | None = None,
    timeout: str = "10m",
) -> None:
    """``helm upgrade`` the release with Grafana on (or off), keeping its values."""
    _ensure_dependencies(chart)
    cmd = ["helm", "upgrade", release, str(chart), "-n", namespace, "--reuse-values"]
    cmd += ["--set", f"grafana.enabled={str(enabled).lower()}"]
    cmd += ["--wait", "--timeout", timeout]
    with tempfile.TemporaryDirectory() as tmp:
        if db is not None:
            source = Path(tmp) / "datasource.yaml"
            source.write_text(yaml.safe_dump(datasource_config(db)))
            cmd += ["--set-file", f"grafana.archiveDatasource={source}"]
        _run(cmd, f"helm upgrade {release}")


def grafana_service(release: str, namespace: str) -> str:
    """The Grafana Service of a release."""
    out = _run(
        ["kubectl", "get", "svc", "-n", namespace, "-o", "name", "-l",
         f"app.kubernetes.io/name=grafana,app.kubernetes.io/instance={release}"],
        "kubectl get svc",
    )  # fmt: skip
    if not out.strip():
        raise GrafanaDeployError(f"No Grafana service for release {release}")
    return out.split()[0]


def port_forward(service: str, namespace: str, port: int) -> None:
    """Forward Grafana to localhost:port until interrupted."""
    subprocess.run(
        ["kubectl", "port-forward", "-n", namespace, service, f"{port}:80"],
        check=False,
    )


# --------------------------------------------------------------------------
# Docker Compose
# --------------------------------------------------------------------------


def container_name(project: str) -> str:
    """Name of the Grafana container for a project."""
    return f"{project}-grafana"


def compose_network(container: str) -> str:
    """The (first) network a container is on."""
    out = _run(
        ["docker", "inspect", "-f",
         "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", container],
        "docker inspect",
    )  # fmt: skip
    networks = out.split()
    if not networks:
        raise GrafanaDeployError(f"{container} isn't on a Docker network")
    return networks[0]


def _provisioning(folder: Path, db: ArchiveDB, dashboard: Path) -> None:
    (folder / "datasources").mkdir(parents=True, exist_ok=True)
    (folder / "dashboards").mkdir(parents=True, exist_ok=True)
    files = folder / "dashboard-files"
    files.mkdir(parents=True, exist_ok=True)
    (folder / "datasources" / "hdbpp.yaml").write_text(
        yaml.safe_dump(datasource_config(db))
    )
    (folder / "dashboards" / "ska-taranta.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": 1,
                "providers": [
                    {
                        "name": "ska-taranta",
                        "type": "file",
                        "options": {
                            "path": "/etc/grafana/provisioning/dashboard-files"
                        },
                    }
                ],
            }
        )
    )
    shutil.copy(dashboard, files / dashboard.name)
    for path in folder.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    folder.chmod(0o755)


def run_container(
    project: str,
    network: str,
    db: ArchiveDB,
    dashboard: Path,
    port: int,
    image: str,
) -> str:
    """(Re)start the project's Grafana container; return its URL."""
    name = container_name(project)
    folder = Path(tempfile.gettempdir()) / f"ska-taranta-{name}"
    shutil.rmtree(folder, ignore_errors=True)
    _provisioning(folder, db, dashboard)
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    _run(
        ["docker", "run", "-d", "--name", name, "--network", network,
         "-p", f"127.0.0.1:{port}:3000",
         "-e", "GF_AUTH_ANONYMOUS_ENABLED=true",
         "-e", "GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer",
         "-e", "GF_SECURITY_ADMIN_PASSWORD=admin",
         "-v", f"{folder}:/etc/grafana/provisioning:ro",
         image],
        "docker run",
        timeout=600,  # may pull the image
    )  # fmt: skip
    url = f"http://localhost:{port}"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{url}/api/health", timeout=2).ok:
                return url
        except requests.RequestException:
            pass
        time.sleep(1)
    raise GrafanaDeployError(
        f"Grafana didn't come up on {url}; see `docker logs {name}`"
    )


def stop_container(project: str) -> bool:
    """Remove the project's Grafana container; return whether there was one."""
    proc = subprocess.run(
        ["docker", "rm", "-f", container_name(project)],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0 and "No such container" not in proc.stderr
