# ska-taranta-setup

Add [Taranta](https://gitlab.com/tango-controls/web/taranta) to an SKA Tango
project, and generate a best-guess set of dashboards from the project's own
devices.

Add it as a dev dependency, run one command, and you get:

* the Taranta, taranta-auth and TangoGQL charts wired into your umbrella chart,
  with values that work on minikube;
* Make targets to create the auth secret (automatically, before every
  install), expose Taranta on `localhost`, and regenerate and upload dashboards;
* an **overview dashboard** (state, health, modes and headline status LEDs for
  every device), plus a **detailed dashboard per device** with every attribute
  and command laid out in sections.

The dashboards are a starting point: import them, then move and delete things
in Taranta's editor as usual.

## Quick start

```bash
uv add --dev ska-taranta-setup         # or: poetry add --group dev ska-taranta-setup
uv run ska-taranta setup               # init + discover + generate
uv run ska-taranta preview             # optional: wireframes in dashboards/preview.html
```

Then deploy as you normally do, and load the dashboards:

```bash
make k8s-install-chart                 # creates the auth secret first (pre-install hook)
make taranta-minikube-setup            # ingress + tunnel + port-forward
make taranta-upload                    # or import dashboards/*.wj in the Taranta UI
```

Taranta is then at `http://localhost:8080/<namespace>/taranta/`
(`make taranta-url`). The default dev login is `user1` / `abc123`.

When your devices change, run `make taranta-dashboards` and commit the
updated `.wj` files.

## Commands

| Command | What it does |
| --- | --- |
| `ska-taranta init` | Adds the Taranta subcharts and values, writes `taranta.mk` and includes it from the `Makefile`, records config in `pyproject.toml`. Idempotent: never overwrites anything you've set. `--dry-run` shows what would change. |
| `ska-taranta discover` | Finds your devices and records their full interfaces in `taranta/devices.json`. `--live` queries running devices instead. |
| `ska-taranta generate` | Writes `dashboards/<project>-overview.wj` and one `.wj` per device. `-d REGEX` limits it to some devices, `--columns N` sets the layout width. |
| `ska-taranta preview` | Draws wireframes of the dashboards into an HTML page, so you can check a layout without deploying. |
| `ska-taranta upload` | Logs in to a running Taranta and creates or updates the dashboards, matched by name. |
| `ska-taranta setup` | `init`, `discover` and `generate` in one go. |

`-C PATH` runs any command against another project; `-v` shows detail.

## How it works

### 1. Which devices are deployed?

In order of preference:

1. `devices` listed in `[tool.ska-taranta-setup]`;
2. `helmfile write-values` for the configured environment (default
   `minikube-ci`), which gives the real per-device properties, including those
   derived from telmodel, such as the SNMP `Model`;
3. the raw `values*.yaml` files in `charts/` and `helmfile.d/`.

Both the `ska-tango-devices` layout (`devices: {Class: {trl: props}}`) and
the older `ska-tango-util` layout (`deviceServers … classes: [...]`) are
understood. `init` turns on any `K8S_DEPLOY_*SIMULATOR*` switches it finds in
your `Makefile` for this render, so that simulated device classes are included.
Simulator device classes themselves are excluded from dashboards by default.

### 2. What does each device look like?

The only reliable way to see every attribute, including the dynamic ones made
in `init_device` (SNMP devices, for example), is to ask a running device. So,
like [`ska-tango-difdoc`](https://gitlab.com/ska-telescope/ska-tango-difdoc),
`discover` **starts each device class locally with no Tango database** (a
`-file=` property file), using the properties it is deployed with, then queries
it over a `DeviceProxy`. No cluster is needed. To make that work offline:

* properties naming other devices (`SatUtcTrl`, `SubServerTrls`, …) are
  dropped, so aggregating devices don't fail to connect;
* host/address properties are pointed at `127.0.0.1`, so devices don't hang
  resolving in-cluster names. Nothing is actually contacted.

Devices of the same class with the same `interface_key_properties` (default
`Model`) are introspected once. `--live` skips all this and queries a running
system through `TANGO_HOST`.

The result is a plain JSON snapshot that you can commit and review, so
generation needs neither pytango nor a running device.

### 3. Which widget for which attribute?

| Attribute | Widget |
| --- | --- |
| `State` | Device status (LED and name) |
| `healthState` | LED, green when OK |
| enum with ok/error-like labels | LED, green on the "good" label |
| other read-only enum | value display with enum labels |
| writable enum (`adminMode`, `controlMode`, …) | dropdown writer |
| boolean | LED (red when true for `*fault*`/`*error*`/`*alarm*`) |
| writable boolean | switch |
| bounded physical quantity (temperature, voltage, power, load, …) | dial, and a trend plot |
| other physical or timing quantity (offsets, delays, …) | value display, and a trend plot |
| counters, identifiers, other numbers and strings | value display |
| writable number or string | writer |
| numeric array | spectrum plot |
| other array | value display (JSON) |
| `healthInfo` | logger |
| commands (not `Init`/`State`/`Status`) | command button; "testing" and `EXPERT` ones go to an Expert section |

Sections (each one a Taranta BOX, so it moves as one unit):

* **Device**: state, health, status, modes, health info, version and logging level;
* one section per **name family** of 3 or more (`pwsl_*`, `tsrc1_*`,
  `net_wr0_*`, `gntp*`, …). Indexed blocks like ports each get their own
  section, and the family prefix is dropped from labels inside it;
* **Status**, **Measurements**, **Information** and **Settings** for the rest,
  then **Commands** and **Expert**.

Sections are packed into columns, shortest column first. Output is
deterministic, so regenerating gives clean diffs.

## Configuration

`init` writes the guessed values; everything is optional.

```toml
[tool.ska-taranta-setup]
chart = "charts/my-project"            # umbrella chart to add Taranta to
dashboards_dir = "dashboards"
snapshot = "taranta/devices.json"
title = "My Project"                   # dashboard name prefix (default: project name)
columns = 4
helmfile_environment = "minikube-ci"
helmfile_env = { K8S_DEPLOY_WR_SIMULATOR = "true" }
exclude_classes = [".*Simulator$"]     # regexes
exclude_devices = []                   # regexes on TRL
interface_key_properties = ["Model"]
tango_db = "taranta"                   # URL segment before /taranta: /<ns>/<tango_db>/...
startup_timeout = 20.0
# Explicit devices, instead of reading helm:
# devices = { MyDevice = ["my/device/1", "my/device/2"] }

# Extra properties for starting a class locally:
[tool.ska-taranta-setup.properties.MyDevice]
SomeMandatoryProperty = "value"
```

`[tool.tangodifdoc.<Class>.properties]` is also read, so a project already
documented with `tangodocgen --auto` needs no extra configuration. Those values
never override the `interface_key_properties` of the deployed device.

## Development

```bash
uv sync
uv run pytest --doctest-modules src tests
uv run ruff check src tests && uv run ruff format --check src tests
```

`tests/fixtures/sat-lmc-devices.json` is a real snapshot from ska-sat-lmc,
used to test generation end to end.

## Known limitations

* `upload` follows Taranta's own import API (taranta-dashboard 2.18) but has
  not yet been tested against a live deployment; importing the `.wj` files in
  the UI is the safe path.
* The chart versions match the ska-sat-lmc `wom-xxx-taranta` branch
  (Taranta 2.18.9, auth 0.3.1, TangoGQL-ariadne 1.0.13); override them with
  `taranta_version`, `taranta_auth_version` and `tangogql_version`.
* Layout assumes Taranta's default 20 px grid (`MIN_WIDGET_SIZE`).
* Dashboard variables (one dashboard switchable between devices of a class)
  aren't generated yet: you get one dashboard per device.
