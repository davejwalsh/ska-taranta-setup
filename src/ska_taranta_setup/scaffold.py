"""
``ska-taranta init``: wire Taranta into a project's helm chart and Makefile.

Mirrors what was done by hand on ska-sat-lmc's ``wom-xxx-taranta`` branch:

* add ``ska-tango-taranta``, ``ska-tango-taranta-auth`` and
  ``ska-tango-tangogql-ariadne`` (as ``tangogql``) to the umbrella chart;
* add their values (ingress off, taranta-auth enabled, ...) to ``values.yaml``;
* add Make targets for the auth secret, minikube access and dashboards;
* record the guessed configuration in ``[tool.ska-taranta-setup]``.

Every step is idempotent: existing keys and dependencies are left alone, so
it is safe to re-run, and it never overwrites a value you've changed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import tomlkit
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from ska_taranta_setup.config import SETTINGS_FILE, TOOL_KEY, Config

SKAO_HELM_REPO = "https://artefact.skao.int/repository/helm-internal"
MAKE_INCLUDE = "-include taranta.mk"


@dataclass
class Report:
    """What ``init`` did (or would do, in dry-run mode)."""

    changes: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    return yaml


def chart_dependencies(config: Config) -> list[dict[str, Any]]:
    """The subcharts Taranta needs."""
    return [
        {
            "name": "ska-tango-taranta",
            "version": config.taranta_version,
            "repository": SKAO_HELM_REPO,
            "condition": "ska-tango-taranta.enabled",
        },
        {
            "name": "ska-tango-taranta-auth",
            "version": config.taranta_auth_version,
            "repository": SKAO_HELM_REPO,
            "condition": "ska-tango-taranta-auth.enabled",
        },
        {
            "name": "ska-tango-tangogql-ariadne",
            "version": config.tangogql_version,
            "repository": SKAO_HELM_REPO,
            "condition": "ska-tango-taranta.enabled",
            "alias": "tangogql",
        },
    ]


def chart_values(project_name: str) -> dict[str, Any]:
    """Values for the Taranta subcharts, as used on ska-sat-lmc."""
    return {
        "global": {
            "minikube": True,
            "use_aws": True,
            "AWS_URL": "https://k8s-services.skao.int",
            "operator": False,
            "labels": {"app": project_name},
        },
        "dsconfig": {
            "image": {
                "registry": "artefact.skao.int",
                "image": "ska-tango-images-tango-dsconfig",
                "tag": "1.8.3",
                "pullPolicy": "IfNotPresent",
            }
        },
        "tangogql": {
            "enabled": True,
            "ska-tango-base": {"enabled": False},
            "ska-tango-examples": {"enabled": False},
        },
        "ska-tango-taranta": {
            "enabled": True,
            "ingress": {"enabled": False, "nginx": False},
            "TANGO_DBS": [],
        },
        "ska-tango-taranta-auth": {"enabled": True, "ingress": {"enabled": False}},
    }


def _commented(value: Any) -> Any:
    """Convert nested dicts to ruamel maps so comments can attach to them."""
    if isinstance(value, dict):
        return CommentedMap((k, _commented(v)) for k, v in value.items())
    return value


def _merge_missing(
    target: CommentedMap, defaults: dict[str, Any], path: str = ""
) -> list[str]:
    """
    Add keys from ``defaults`` that ``target`` lacks; never overwrite.

    Blank lines and comments trailing the map's last key are kept at the end
    of the map, rather than ending up between the old keys and the new ones.
    """
    added = []
    last_key = next(reversed(target), None) if target else None
    trailing = target.ca.items.pop(last_key, None) if last_key is not None else None
    new_keys = []
    for key, value in defaults.items():
        dotted = f"{path}.{key}" if path else key
        if key not in target:
            target[key] = _commented(value)
            added.append(dotted)
            new_keys.append(key)
        elif isinstance(value, dict) and isinstance(target[key], dict):
            added.extend(_merge_missing(target[key], value, dotted))
    if trailing is not None:
        # Re-attach to the (deepest) last key, wherever the map now ends.
        holder: CommentedMap = target
        final = next(reversed(holder))
        while isinstance(holder[final], CommentedMap) and holder[final]:
            holder = holder[final]
            final = next(reversed(holder))
        holder.ca.items[final] = trailing
    if new_keys and not path:
        target.yaml_set_comment_before_after_key(
            new_keys[0], before="\nTaranta (added by ska-taranta-setup)"
        )
    return added


def update_chart(config: Config, report: Report, dry_run: bool) -> bool:
    """Add Taranta dependencies to Chart.yaml; return whether it changed."""
    chart = config.chart_path
    if chart is None or not (chart / "Chart.yaml").is_file():
        report.warnings.append(
            "No helm chart found; set `chart` in [tool.ska-taranta-setup] and re-run."
        )
        return False
    yaml = _yaml()
    path = chart / "Chart.yaml"
    data = yaml.load(path.read_text())
    deps = data.setdefault("dependencies", [])
    existing = {d.get("alias") or d.get("name") for d in deps}
    added = []
    for dep in chart_dependencies(config):
        key = dep.get("alias") or dep["name"]
        if key in existing:
            report.skipped.append(
                f"{path.relative_to(config.root)}: {key} already a dependency"
            )
            continue
        deps.append(dep)
        added.append(key)
    if added:
        report.changes.append(
            f"{path.relative_to(config.root)}: added dependencies {', '.join(added)}"
        )
        if not dry_run:
            with path.open("w") as fh:
                yaml.dump(data, fh)
    return bool(added)


def update_values(config: Config, report: Report, dry_run: bool) -> None:
    """Add Taranta values to the chart's values.yaml (and schema, if strict)."""
    chart = config.chart_path
    if chart is None or not (chart / "values.yaml").is_file():
        return
    yaml = _yaml()
    path = chart / "values.yaml"
    data = yaml.load(path.read_text()) or CommentedMap()
    defaults = chart_values(config.project_name)
    added = _merge_missing(data, defaults)
    rel = path.relative_to(config.root)
    if added:
        report.changes.append(f"{rel}: added {', '.join(added)}")
        if not dry_run:
            with path.open("w") as fh:
                yaml.dump(data, fh)
    else:
        report.skipped.append(f"{rel}: Taranta values already present")

    schema_path = chart / "values.schema.json"
    if schema_path.is_file():
        schema = json.loads(schema_path.read_text())
        if schema.get("additionalProperties") is False:
            props = schema.setdefault("properties", {})
            new = [k for k in defaults if k not in props]
            for key in new:
                props[key] = {"type": "object"}
            if new:
                report.changes.append(
                    f"{schema_path.relative_to(config.root)}: allowed {', '.join(new)}"
                )
                if not dry_run:
                    schema_path.write_text(json.dumps(schema, indent=2) + "\n")


def run_prefix(root: Path) -> str:
    """How to run project tools: ``uv run`` or ``poetry run``."""
    if (root / "uv.lock").is_file():
        return "uv run"
    if (root / "poetry.lock").is_file():
        return "poetry run"
    return ""


def update_makefile(config: Config, report: Report, dry_run: bool, force: bool) -> None:
    """Write taranta.mk and include it from the Makefile."""
    mk = config.root / "taranta.mk"
    template = resources.files("ska_taranta_setup.templates").joinpath("taranta.mk")
    content = template.read_text().replace("{run_prefix}", run_prefix(config.root))
    if mk.exists() and not force:
        report.skipped.append("taranta.mk: exists (use --force to regenerate)")
    else:
        report.changes.append("taranta.mk: written")
        if not dry_run:
            mk.write_text(content)

    makefile = config.root / "Makefile"
    if not makefile.is_file():
        report.warnings.append(
            "No Makefile; include taranta.mk from your build manually."
        )
        return
    text = makefile.read_text()
    if MAKE_INCLUDE in text:
        report.skipped.append("Makefile: already includes taranta.mk")
        return
    block = f"\n# Taranta (ska-taranta-setup)\n{MAKE_INCLUDE}\n"
    # Keep PrivateRules.mak last so personal overrides still win.
    marker = "-include PrivateRules.mak"
    if marker in text:
        text = text.replace(marker, block.lstrip("\n") + "\n" + marker, 1)
    else:
        text = text.rstrip("\n") + "\n" + block
    report.changes.append("Makefile: include taranta.mk")
    if not dry_run:
        makefile.write_text(text)


def guess_helmfile_env(root: Path) -> dict[str, str]:
    """
    Turn on simulators when rendering, so every device class is seen.

    Looks for ``K8S_DEPLOY_*SIMULATOR*`` switches in the Makefile.
    """
    makefile = root / "Makefile"
    if not makefile.is_file():
        return {}
    env = {}
    for line in makefile.read_text().splitlines():
        name = line.split("?=")[0].split(":=")[0].split("=")[0].strip()
        if name.startswith("K8S_DEPLOY_") and "SIMULATOR" in name and " " not in name:
            env[name] = "true"
    return env


def _settings_table(config: Config) -> tomlkit.items.Table:
    """The guessed settings, as written by ``init``."""
    table = tomlkit.table()
    table.add(
        tomlkit.comment(
            "Generated by `ska-taranta init`; see ska-taranta-setup README."
        )
    )
    table["chart"] = config.chart
    table["dashboards_dir"] = config.dashboards_dir
    table["helmfile_environment"] = config.helmfile_environment
    env = guess_helmfile_env(config.root)
    if env:
        inline = tomlkit.inline_table()
        inline.update(env)
        table["helmfile_env"] = inline
    table["exclude_classes"] = config.exclude_classes
    table["tango_db"] = config.tango_db
    table.add(tomlkit.nl())  # keep a blank line before the next table
    return table


def update_pyproject(config: Config, report: Report, dry_run: bool) -> None:
    """
    Record the guessed configuration.

    In ``[tool.ska-taranta-setup]`` of ``pyproject.toml`` for Python projects,
    or in ``ska-taranta.toml`` for others (e.g. C++ with CMake).
    """
    path = config.root / "pyproject.toml"
    if not path.is_file():
        standalone = config.root / SETTINGS_FILE
        if standalone.is_file():
            report.skipped.append(f"{SETTINGS_FILE}: already present")
            return
        doc = tomlkit.document()
        for key, value in _settings_table(config).items():
            doc[key] = value
        report.changes.append(f"{SETTINGS_FILE}: written (no pyproject.toml)")
        if not dry_run:
            standalone.write_text(tomlkit.dumps(doc))
        return
    doc = tomlkit.parse(path.read_text())
    tool = doc.setdefault("tool", tomlkit.table())
    if TOOL_KEY in tool:
        report.skipped.append(f"pyproject.toml: [tool.{TOOL_KEY}] already present")
        return
    tool[TOOL_KEY] = _settings_table(config)
    report.changes.append(f"pyproject.toml: added [tool.{TOOL_KEY}]")
    if not dry_run:
        path.write_text(tomlkit.dumps(doc))


def helm_dependency_update(config: Config, report: Report) -> None:
    """Refresh Chart.lock so the new subcharts are pinned."""
    chart = config.chart_path
    if chart is None or shutil.which("helm") is None:
        report.warnings.append(
            "helm not found; run `helm dependency update` on the chart."
        )
        return
    proc = subprocess.run(
        ["helm", "dependency", "update", str(chart)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        report.changes.append(
            f"{chart.relative_to(config.root)}: helm dependency update"
        )
    else:
        report.warnings.append(
            "`helm dependency update` failed (offline?); run it before deploying:\n"
            + proc.stderr.strip()[-1000:]
        )


def init_project(
    config: Config, dry_run: bool = False, force: bool = False, helm_update: bool = True
) -> Report:
    """Wire Taranta into the project at ``config.root``."""
    report = Report()
    chart_changed = update_chart(config, report, dry_run)
    update_values(config, report, dry_run)
    update_makefile(config, report, dry_run, force)
    update_pyproject(config, report, dry_run)
    gitkeep = config.dashboards_path / ".gitkeep"
    if not config.dashboards_path.exists():
        report.changes.append(f"{config.dashboards_dir}/: created")
        if not dry_run:
            config.dashboards_path.mkdir(parents=True)
            gitkeep.touch()
    if chart_changed and helm_update and not dry_run:
        helm_dependency_update(config, report)
    return report
