"""The ``ska-taranta`` command line."""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

import click

from ska_taranta_setup.config import Config, load_config
from ska_taranta_setup.dashboard import LayoutOptions, slugify, write_dashboards
from ska_taranta_setup.model import DeviceInstance, Snapshot
from ska_taranta_setup.options import OptionsError, build


def _config(ctx: click.Context) -> Config:
    return ctx.obj["config"]


def _rel(config: Config, path: Path) -> Path:
    """Show paths inside the project relative to it."""
    return path.relative_to(config.root) if path.is_relative_to(config.root) else path


@click.group()
@click.option(
    "--project",
    "-C",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=".",
    show_default=True,
    help="Root of the project to work on.",
)
@click.option("--verbose", "-v", is_flag=True, help="Show what's happening.")
@click.pass_context
def main(ctx: click.Context, project: Path, verbose: bool) -> None:
    """Add Taranta to an SKA Tango project and generate dashboards for it."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    try:
        ctx.obj = {"config": load_config(project)}
    except OptionsError as exc:
        raise click.ClickException(f"pyproject.toml: {exc}") from exc


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------


@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would change; change nothing.")
@click.option("--force", is_flag=True, help="Regenerate taranta.mk even if it exists.")
@click.option(
    "--no-helm-update", is_flag=True, help="Don't run `helm dependency update`."
)
@click.pass_context
def init(ctx: click.Context, dry_run: bool, force: bool, no_helm_update: bool) -> None:
    """Add the Taranta charts, values and Make targets to the project."""
    from ska_taranta_setup.scaffold import init_project

    config = _config(ctx)
    report = init_project(
        config, dry_run=dry_run, force=force, helm_update=not no_helm_update
    )
    prefix = "would change" if dry_run else "changed"
    for line in report.changes:
        click.secho(f"  {prefix}: {line}", fg="green")
    for line in report.skipped:
        click.echo(f"  unchanged: {line}")
    for line in report.warnings:
        click.secho(f"  warning: {line}", fg="yellow")
    if not dry_run and report.changes:
        click.echo(
            "\nNext: `ska-taranta setup` (or `make taranta-dashboards`) to generate "
            "dashboards, then deploy as usual."
        )


# --------------------------------------------------------------------------
# discover
# --------------------------------------------------------------------------


def _discover(
    config: Config, live: bool, tango_host: str | None
) -> tuple[Snapshot, list[str]]:
    from ska_taranta_setup.instances import discover_instances
    from ska_taranta_setup.introspect import introspect_live, introspect_local

    if tango_host:
        config.tango_host = tango_host
    devices, source = discover_instances(config)
    click.echo(f"Found {len(devices)} device(s) from {source}.")
    excluded = [d for d in devices if config.is_excluded(d.class_name, d.trl)]
    devices = [d for d in devices if not config.is_excluded(d.class_name, d.trl)]
    if excluded:
        click.echo(
            f"Excluding {len(excluded)}: "
            + ", ".join(sorted({d.class_name for d in excluded}))
        )
    if live:
        host = config.tango_host or os.environ.get("TANGO_HOST", "?")
        click.echo(f"Querying running devices via TANGO_HOST={host}...")
        snapshot, errors = introspect_live(config, devices)
    else:
        if not devices:
            raise click.ClickException(
                "No devices found. Set `devices` in [tool.ska-taranta-setup], "
                "check the helmfile environment, or use --live."
            )
        click.echo(
            "Starting each device locally (no Tango DB) to read its interface..."
        )
        snapshot, errors = introspect_local(config, devices)
    return snapshot, errors


def _print_summary(snapshot: Snapshot) -> None:
    rows = []
    for device in snapshot.devices:
        interface = snapshot.interface_for(device)
        n_attr = len(interface.attributes) if interface else "-"
        n_cmd = len(interface.commands) if interface else "-"
        rows.append((device.trl, device.class_name, str(n_attr), str(n_cmd)))
    widths = [
        max(len(r[i]) for r in [("DEVICE", "CLASS", "ATTRS", "CMDS"), *rows])
        for i in range(4)
    ]
    header = ("DEVICE", "CLASS", "ATTRS", "CMDS")
    for row in [header, *rows]:
        click.echo(
            "  " + "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True))
        )


@main.command()
@click.option("--live", is_flag=True, help="Query devices that are already running.")
@click.option("--tango-host", help="TANGO_HOST for --live (host:port).")
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Snapshot file (default from config: taranta/devices.json).",
)
@click.pass_context
def discover(
    ctx: click.Context, live: bool, tango_host: str | None, output: Path | None
) -> None:
    """Find the project's devices and record their interfaces."""
    config = _config(ctx)
    snapshot, errors = _discover(config, live, tango_host)
    path = output or config.snapshot_path
    snapshot.save(path)
    _print_summary(snapshot)
    for error in errors:
        click.secho(f"  failed: {error.splitlines()[0]}", fg="yellow", err=True)
    click.echo(f"Saved {_rel(config, path)}")
    if errors and not snapshot.interfaces:
        sys.exit(1)


# --------------------------------------------------------------------------
# generate
# --------------------------------------------------------------------------


def _select(
    devices: list[DeviceInstance], patterns: tuple[str, ...]
) -> list[DeviceInstance]:
    if not patterns:
        return devices
    return [
        d
        for d in devices
        if any(re.search(p, d.trl) or re.fullmatch(p, d.class_name) for p in patterns)
    ]


def _generate(
    config: Config,
    snapshot: Snapshot,
    devices: tuple[str, ...],
    out: Path | None,
    columns: int | None,
) -> list[Path]:
    selected = [
        d
        for d in _select(snapshot.devices, devices)
        if d.interface and not config.is_excluded(d.class_name, d.trl)
    ]
    if not selected:
        raise click.ClickException(
            "No introspected devices to generate dashboards for."
        )
    try:
        opts = build(LayoutOptions, config.layout, "tool.ska-taranta-setup.layout")
    except (OptionsError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    opts.tile_size = config.layout.get("tile_size", config.tile_size)
    if opts.logo:
        opts.logo = str((config.root / opts.logo).resolve())
        if not Path(opts.logo).is_file():
            raise click.ClickException(f"layout.logo: {opts.logo} not found")
    opts.columns = columns or config.layout.get("columns", config.columns)
    return write_dashboards(
        config.title,
        selected,
        snapshot,
        out or config.dashboards_path,
        config.tango_db,
        opts,
        config.generate,
        config.subsystems,
    )


@main.command()
@click.option(
    "--snapshot",
    "snapshot_path",
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
    help="Snapshot to read (default from config).",
)
@click.option("--output-dir", "-o", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--device",
    "-d",
    "devices",
    multiple=True,
    help="Only these devices (regex on TRL, or exact class name). Repeatable.",
)
@click.option("--columns", type=int, help="Columns to pack sections into.")
@click.option(
    "--device-pages/--no-device-pages",
    default=None,
    help="Also write a detailed page per device (default: on, or "
    "`generate.device_dashboards` in pyproject.toml).",
)
@click.pass_context
def generate(
    ctx: click.Context,
    snapshot_path: Path | None,
    output_dir: Path | None,
    devices: tuple[str, ...],
    columns: int | None,
    device_pages: bool | None,
) -> None:
    """
    Write Taranta dashboards (.wj) from the discovered devices.

    Always an overview, plus a page per subsystem (worked out from device
    references, or `[[subsystems]]` in pyproject.toml), plus, unless
    --no-device-pages, a detailed page per device. Pages link to each other.
    """
    config = _config(ctx)
    if device_pages is not None:
        config.generate.device_dashboards = device_pages
    path = snapshot_path or config.snapshot_path
    if not path.is_file():
        raise click.ClickException(
            f"{path} not found; run `ska-taranta discover` first."
        )
    written = _generate(config, Snapshot.load(path), devices, output_dir, columns)
    for file in written:
        click.echo(f"  wrote {_rel(config, file)}")
    click.echo(
        "Import them in Taranta (Dashboards > Import) or run `ska-taranta upload`."
    )


# --------------------------------------------------------------------------
# setup (all of the above)
# --------------------------------------------------------------------------


@main.command()
@click.option(
    "--live", is_flag=True, help="Query running devices instead of starting them."
)
@click.option(
    "--no-helm-update", is_flag=True, help="Don't run `helm dependency update`."
)
@click.pass_context
def setup(ctx: click.Context, live: bool, no_helm_update: bool) -> None:
    """Init + discover + generate, in one go."""
    ctx.invoke(init, no_helm_update=no_helm_update)
    config = load_config(_config(ctx).root)  # re-read: init may have written config
    ctx.obj["config"] = config
    click.echo("")
    ctx.invoke(discover, live=live)
    click.echo("")
    ctx.invoke(generate)


# --------------------------------------------------------------------------
# attributes
# --------------------------------------------------------------------------


@main.command()
@click.option(
    "--device",
    "-d",
    "devices",
    multiple=True,
    help="Only these devices (regex on TRL, or exact class name). Repeatable.",
)
@click.option("--hidden", is_flag=True, help="List only the attributes left out.")
@click.pass_context
def attributes(ctx: click.Context, devices: tuple[str, ...], hidden: bool) -> None:
    """
    List every attribute and where it goes on the dashboards.

    For each device class: each attribute's section and widget(s), and for
    the ones left out, why. Use it to choose what to remove with
    `exclude_attributes_by_class` (or `exclude_attributes` for every class).
    """
    from ska_taranta_setup.heuristics import attribute_plan

    config = _config(ctx)
    if not config.snapshot_path.is_file():
        raise click.ClickException(
            f"{_rel(config, config.snapshot_path)} not found; run "
            "`ska-taranta discover` first."
        )
    snapshot = Snapshot.load(config.snapshot_path)
    by_interface: dict[str, list[DeviceInstance]] = {}
    for device in _select(snapshot.devices, devices):
        if device.interface and not config.is_excluded(device.class_name, device.trl):
            by_interface.setdefault(device.interface, []).append(device)
    for key, members in by_interface.items():
        interface = snapshot.interfaces[key]
        plans = attribute_plan(interface, config.generate)
        shown = sum(1 for p in plans if not p.hidden)
        click.secho(
            f"\n{interface.class_name}  ({', '.join(d.trl for d in members)})",
            bold=True,
        )
        click.echo(f"  {shown} of {len(plans)} attributes shown")
        width = max(len(p.name) for p in plans) + 2
        section = None
        for plan in sorted((p for p in plans if not p.hidden), key=lambda p: p.order):
            if hidden:
                break
            if plan.section != section:
                section = plan.section
                click.secho(f"  {section}", fg="cyan")
            click.echo(f"    {plan.name:<{width}}{' + '.join(plan.widgets)}")
        left_out = [p for p in plans if p.hidden]
        if left_out:
            click.secho("  Left out", fg="yellow")
            for plan in left_out:
                click.echo(f"    {plan.name:<{width}}{plan.hidden}")
    click.echo(
        "\nTo leave attributes out, add regexes (any case) to pyproject.toml:\n\n"
        "  [tool.ska-taranta-setup.generate.exclude_attributes_by_class]\n"
        '  SatWhiteRabbit = ["net_wr1[0-5]_.*", "hdd.*"]\n\n'
        "then run `ska-taranta generate`."
    )


# --------------------------------------------------------------------------
# preview
# --------------------------------------------------------------------------


@main.command()
@click.argument(
    "files", nargs=-1, type=click.Path(dir_okay=False, exists=True, path_type=Path)
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    help="HTML file to write (default: <dashboards_dir>/preview.html).",
)
@click.pass_context
def preview(ctx: click.Context, files: tuple[Path, ...], output: Path | None) -> None:
    """Draw wireframes of the dashboards into an HTML page (no Taranta needed)."""
    from ska_taranta_setup.preview import write_preview

    config = _config(ctx)
    paths = list(files) or sorted(config.dashboards_path.glob("*.wj"))
    if not paths:
        raise click.ClickException("No dashboards; run `ska-taranta generate` first.")
    out = write_preview(
        paths, output or config.dashboards_path / "preview.html", config.tile_size
    )
    click.echo(f"Wrote {_rel(config, out)}")


# --------------------------------------------------------------------------
# upload
# --------------------------------------------------------------------------


@main.command()
@click.argument(
    "files", nargs=-1, type=click.Path(dir_okay=False, exists=True, path_type=Path)
)
@click.option(
    "--url",
    default=lambda: os.environ.get("TARANTA_URL", ""),
    help="Taranta URL, e.g. http://localhost:8080/<namespace>/taranta/ ($TARANTA_URL).",
)
@click.option(
    "--user",
    envvar="TARANTA_USER",
    default=None,
    help="Taranta user ($TARANTA_USER; default: `taranta_user` in pyproject.toml, "
    "else user1).",
)
@click.option(
    "--password", envvar="TARANTA_PASSWORD", default=None, help="($TARANTA_PASSWORD)."
)
@click.option(
    "--token",
    envvar="TARANTA_JWT",
    default=None,
    help="Upload as yourself: the `taranta_jwt` cookie from a logged-in browser "
    "($TARANTA_JWT). Overrides --user/--password.",
)
@click.pass_context
def upload(
    ctx: click.Context,
    files: tuple[Path, ...],
    url: str,
    user: str | None,
    password: str | None,
    token: str | None,
) -> None:
    """
    Upload dashboards to a running Taranta (all generated ones by default).

    Dashboards go to the library of the account you upload as, matched by
    name, so re-uploading updates them. That's `taranta_user` from
    pyproject.toml (default: the shared dev account user1), --user, or
    whoever --token belongs to.
    """
    from ska_taranta_setup.upload import (
        DEFAULT_PASSWORD,
        DEFAULT_USER,
        TarantaClient,
        UploadError,
        UploadResult,
        upload_files,
    )

    config = _config(ctx)
    if not url:
        namespace = os.environ.get("KUBE_NAMESPACE", config.project_name)
        url = f"http://localhost:8080/{namespace}/taranta/"
    if files:
        paths = list(files)
    else:
        # Only the dashboards ska-taranta generated (named "<title>-..."): a
        # project may keep its own hand-made ones in the same folder.
        prefix = f"{slugify(config.title)}-"
        every = sorted(config.dashboards_path.glob("*.wj"))
        paths = [p for p in every if p.name.startswith(prefix)]
        others = [p.name for p in every if p not in paths]
        if others:
            click.echo(
                f"Skipping {len(others)} dashboard(s) not generated here "
                f"({', '.join(others)}); pass them as arguments to upload them."
            )
    if not paths:
        raise click.ClickException(
            "No dashboards to upload; run `ska-taranta generate`."
        )
    try:
        if token:
            client = TarantaClient.from_token(url, token, config.tango_db)
        else:
            user = user or config.taranta_user or DEFAULT_USER
            if password is None:
                password = (
                    DEFAULT_PASSWORD
                    if user == DEFAULT_USER
                    else click.prompt(f"Taranta password for {user}", hide_input=True)
                )
            client = TarantaClient.login(url, user, password, config.tango_db)
        username = client.whoami()
        click.echo(
            f"Uploading {len(paths)} dashboard(s) as {username} via {client.base}"
        )

        def report(result: UploadResult) -> None:
            action = "created" if result.created else "updated"
            click.echo(f"  {action}: {result.name}")
            click.echo(f"           {result.url}")

        upload_files(client, paths, progress=report)
    except (UploadError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"\nThey're in {username}'s dashboard library: log in to Taranta as "
        f"{username} to see them"
        + (" (or pass --token to upload as yourself)." if not token else ".")
    )


if __name__ == "__main__":
    main()
