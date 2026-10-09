# ska-taranta-setup

Add [Taranta](https://gitlab.com/tango-controls/web/taranta) to an SKA Tango
project, and generate a best-guess set of dashboards from the project's own
devices.

Add it as a dev dependency, run one command, and you get:

* the Taranta, taranta-auth and TangoGQL charts wired into your umbrella chart,
  with values that work on minikube;
* Make targets to create the auth secret (automatically, before every
  install), expose Taranta on `localhost`, and regenerate and upload dashboards;
* linked dashboards: an **overview** with a status bar for every device, a
  page per **subsystem** (worked out from how your devices refer to each other,
  or configured) with its own status bar and a band per device, and a
  **detailed page per device** with every attribute and command. Buttons link
  them all up and down.

The dashboards are a starting point: import them, then move and delete things
in Taranta's editor as usual.

## Installing

You don't need to download or clone anything: uv installs it
straight from GitHub.

**Python projects** add it as a dev dependency:

```bash
uv add --dev "ska-taranta-setup @ git+https://github.com/davejwalsh/ska-taranta-setup.git"
```

**C++ projects** (no Python environment of their own) install the
`ska-taranta` command as a uv tool, with pytango:

```bash
uv tool install "ska-taranta-setup[tango] @ git+https://github.com/davejwalsh/ska-taranta-setup.git"
```

That's all a C++ project needs: don't run `uv add` or `uv lock` there, as they
need a `pyproject.toml`. Run the command directly, e.g. `ska-taranta setup`.

Then follow the [step by step](#step-by-step) guide.

## Step by step

These steps work for any SKA Tango project that deploys with Helm (via
`make k8s-install-chart`, with or without helmfile). They're written for a
Python project; in a **C++ project**, skip step 2 (you installed the tool in
[Installing](#installing)) and run `ska-taranta …` without the `uv run`
prefix. See also [C++ (CMake) projects](#c-cmake-projects). `<project>` is your
repository; `<namespace>` is the Kubernetes namespace it deploys to
(`KUBE_NAMESPACE`, usually the project name).

### 0. Prerequisites

* minikube, kubectl and helm (and helmfile, if your project uses it);
* the project's own Python environment, managed with uv. `ska-taranta`
  runs inside it so it can import your device classes; pytango comes with
  your project;
* a Taranta account, or the shared dev account (see
  [Which account do dashboards go to?](#which-account-do-dashboards-go-to)).

### 1. Make a branch

```bash
cd <project> && git switch -c <ticket>-taranta && git submodule update --init
```

The submodule step matters if your helmfile templates live in a submodule
(e.g. `helmfile.d/.deploy`): device discovery renders them.

### 2. Add ska-taranta-setup as a dev dependency (Python projects)

C++ projects: skip this step.

It installs straight from GitHub; there's nothing to download first:

```bash
uv add --dev "ska-taranta-setup @ git+https://github.com/davejwalsh/ska-taranta-setup.git"
```

This records the Git URL in `pyproject.toml` (under `[tool.uv.sources]`) and
the exact commit in `uv.lock`, so everyone on the project, and CI, gets the
same version.

To pin a particular commit (or, once releases are tagged, a tag) rather than
the latest `main`, add `--rev <commit>` or `--tag <tag>` to the `uv add`
command.

**Updating** to the latest version later:

```bash
uv lock --upgrade-package ska-taranta-setup && uv sync
```

### 3. Set up Taranta and generate the dashboards

```bash
uv run ska-taranta setup
```

This runs three steps (each is also its own command):

1. **`init`** adds the Taranta, taranta-auth and TangoGQL subcharts and their
   values to your umbrella chart, writes `taranta.mk` and includes it from the
   `Makefile`, and records its settings under `[tool.ska-taranta-setup]` in
   `pyproject.toml`. It only adds things, never overwrites, and is safe to
   re-run. Use `--dry-run` to see what it would do.
2. **`discover`** finds the devices you deploy (from your helm/helmfile
   values), starts each device class locally with no Tango database to read
   its full interface, and saves it all to `taranta/devices.json`. Nothing is
   contacted: hosts are pointed at `127.0.0.1`.
3. **`generate`** writes the linked dashboards to `dashboards/`.

Check what it did with `git diff`. If a device class failed to start, see
[Troubleshooting](#troubleshooting).

### 4. Choose your Taranta account

Add this to the `[tool.ska-taranta-setup]` table that `init` created:

```toml
taranta_user = "<your Taranta user>"
```

The password is never stored: `upload` prompts for it, or reads
`$TARANTA_PASSWORD`. Without this setting, dashboards go to the shared dev
account `user1`.

### 5. Optionally, tune the generation, then preview

Subsystems are worked out automatically. You can also define them, turn off
the per-device pages (`generate --no-device-pages`), override widgets, and so
on: see [Generation options](#generation-options). To check a layout without
deploying, write wireframes of every page to `dashboards/preview.html`:

```bash
uv run ska-taranta preview
```

### 6. Deploy

Use your project's usual install target. The Taranta auth secret is created
automatically first, as a `k8s-pre-install-chart` hook:

```bash
minikube start
```
```bash
make k8s-install-chart
```
```bash
kubectl get pods -n <namespace> -w
```

Wait until every pod is `Running` or `Completed`, then press Ctrl-C.

### 7. Expose Taranta on localhost

This enables the minikube ingress and starts a tunnel and a port-forward. It
may ask for sudo for the tunnel.

```bash
make taranta-minikube-setup
```

### 8. Upload the dashboards and open them

```bash
make taranta-upload
```

It logs in, creates or updates every dashboard (matched by name, so links
between pages keep working), and prints a link to each. Open the
**Overview** link (or `make taranta-url`) and log in as the same account.
From there, *Open <subsystem>* and *Details* go down the hierarchy and
*Overview* / *<subsystem>* come back up.

You can import `dashboards/*.wj` through the Taranta UI instead, but don't
re-import a name that already exists: see
[Troubleshooting](#troubleshooting).

### C++ (CMake) projects

The same commands work for C++ device servers, e.g. ska-low-rfi-monitor.
`ska-taranta` detects what each device class is:

* a **Python** class in the project is run directly, as above;
* otherwise it's a compiled server: `discover` uses a **built executable**
  named after the server (e.g. `RFIMonitor`) under `build/`,
  `cmake-build-*/` or `out/build/` if there is one;
* otherwise it runs the server from its **container image**, the one the
  chart deploys (image, executable and environment are read from the
  rendered chart). This needs Docker, but no C++ toolchain or matching
  cppTango, and is usually the most reliable.

As with Python, the server starts with no Tango database, from a property
file; hosts and IP addresses in its properties and environment (e.g. the RFI
monitor's `device_ip`, a real instrument) are pointed at `127.0.0.1`, so no
hardware is contacted.

A C++ repository has no Python environment of its own, so install
`ska-taranta` as a tool, with pytango (the `[tango]` extra), straight from
GitHub:

```bash
uv tool install "ska-taranta-setup[tango] @ git+https://github.com/davejwalsh/ska-taranta-setup.git"
```

This puts `ska-taranta` on your `PATH`; update it later with
`uv tool upgrade ska-taranta-setup`. To try it without installing, prefix
each command with `uvx --from "ska-taranta-setup[tango] @ git+https://github.com/davejwalsh/ska-taranta-setup.git"`.
Then run the same steps in the project: `ska-taranta setup`,
`make taranta-upload`. With no
`pyproject.toml`, `init` keeps its settings in **`ska-taranta.toml`** at the
repository root: the same keys as `[tool.ska-taranta-setup]`, at the top
level (`[generate]`, `[layout]`, `[[subsystems]]`).

`difdoc` (`ska-tango-difdoc`) only documents C++ devices that are already
running (`tangodocgen --device`); `ska-taranta discover --live` does the same
against a deployment, for any language.

### Day to day

| When | Run |
| --- | --- |
| Devices or their attributes changed | `make taranta-dashboards`, then `make taranta-upload` |
| Only `[tool.ska-taranta-setup]` settings changed | `uv run ska-taranta generate`, then `make taranta-upload` |
| Against a running system rather than local servers | `make taranta-dashboards-live` (uses `TANGO_HOST`) |
| Finished for the day | `make taranta-minikube-teardown`, then `make k8s-uninstall-chart` |

Commit `taranta/devices.json` and `dashboards/*.wj` alongside code changes
that affect them, so the dashboards are reviewed with the code. Generation
is deterministic, so the diffs show only real changes.

### Which account do dashboards go to?

With the default `global.use_aws: true`, Taranta in minikube doesn't store
dashboards in the cluster: it proxies login and dashboards to the **shared
SKAO services** (`k8s-services.skao.int`). So uploaded dashboards land in the
library of whichever account you upload as, and survive tearing minikube down.

* By default that's the shared dev account `user1` (password `abc123`), which
  anyone can log in as. Don't upload anything you wouldn't want shared.
* To use your own account, set `taranta_user` (step 4), or one-off:
  `ska-taranta upload --user <user>`.
* If your account signs in with Microsoft SSO (no password), log in to Taranta
  in a browser, copy the `taranta_jwt` cookie, and use
  `TARANTA_JWT=<cookie> ska-taranta upload` (or `--token`).

### Troubleshooting

* **No devices found.** `discover` renders your helmfile environment
  (`helmfile_environment`, default `minikube-ci`). Set the right one, or list
  devices explicitly with `devices = { MyDevice = ["my/device/1"] }`.
* **A device class didn't start** (`discover` reports it). It probably needs a
  mandatory property that the deployment doesn't provide offline. Add it under
  `[tool.ska-taranta-setup.properties.<Class>]`, raise `startup_timeout`, or
  use `discover --live` against a deployment.
* **Simulator or helper devices on the dashboards.** Add them to
  `exclude_classes` / `exclude_devices` (`.*Simulator$` is excluded by default).
* **A link button does nothing, or opens an old version.** Links find their
  target dashboard by name in *your* library. Upload all the pages together,
  with the account you view them with. Importing an existing name in the UI
  creates `<name> copy 1`; delete stale copies in Taranta's dashboard library,
  then run `make taranta-upload` again.
* **Everything looks half or double size.** The layout is sized for a grid of
  `tile_size` pixels (10, as in the SKA Taranta image). Check
  `MIN_WIDGET_SIZE` in `http://localhost:8080/<namespace>/config.js` and set
  `tile_size` to match.
* **Blank space under the mode dropdowns.** That's deliberate: Taranta clips
  a dropdown's menu to the widget, so each one reserves room to open its menu.

## Commands

| Command | What it does |
| --- | --- |
| `ska-taranta init` | Adds the Taranta subcharts and values, writes `taranta.mk` and includes it from the `Makefile`, records config in `pyproject.toml`. Idempotent: never overwrites anything you've set. `--dry-run` shows what would change. |
| `ska-taranta discover` | Finds your devices and records their full interfaces in `taranta/devices.json`. `--live` queries running devices instead. |
| `ska-taranta generate` | Writes the linked dashboards: `<project>-overview.wj`, one `<project>-subsystem-<name>.wj` per subsystem and one `.wj` per device. `--no-device-pages` skips the per-device pages; `-d REGEX` limits it to some devices; `--screen-width PX` fills a different screen width; `--columns N` fixes the column count. |
| `ska-taranta preview` | Draws wireframes of the dashboards into an HTML page, so you can check a layout without deploying. |
| `ska-taranta attributes` | Lists every attribute of every device class: the section and widget(s) it gets, and for the ones left out, why. Use it to choose what to exclude. `-d REGEX` limits it; `--hidden` shows only what's left out. |
| `ska-taranta upload` | Logs in to a running Taranta and creates or updates the generated dashboards (matched by name), printing a link to each. Hand-made `.wj` files in the same folder are skipped unless named as arguments. `--user`, `--token`: see [Which account](#which-account-do-dashboards-go-to). |
| `ska-taranta setup` | `init`, `discover` and `generate` in one go. |

`-C PATH` runs any command against another project; `-v` shows detail.

## How it works

### 1. Which devices are deployed?

In order of preference:

1. `devices` listed in `[tool.ska-taranta-setup]`;
2. `helmfile write-values` for the configured environment (default
   `minikube-ci`), which gives the real per-device properties, including those
   derived from telmodel, such as the SNMP `Model`;
3. `helm template` of the umbrella chart. Charts built on `ska-tango-util`
   render a dsconfig `configuration.json` listing every server, class, device
   and property, however the chart builds them (e.g. ska-mid-wms makes one
   device per entry of a `station_ids` list from a config-file template);
4. the raw `values*.yaml` files in `charts/` and `helmfile.d/`.

Both the `ska-tango-devices` layout (`devices: {Class: {trl: props}}`) and
the older `ska-tango-util` layout (`deviceServers … classes: [...]`) are
understood. `init` turns on any `K8S_DEPLOY_*SIMULATOR*` switches it finds in
your `Makefile` for this render, so that simulated device classes are included.
Simulator device classes themselves are excluded from dashboards by default,
and Tango's own classes (TangoTest, DataBaseds, ...) always are.

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

### 3. How are the pages organised?

```
Overview ──────────── status bar: every device (State, health, headline LEDs)
 │                    tiles: umbrella + standalone devices [Details],
 │                           one per subsystem [Open UTC]
 ├── UTC ──────────── status bar: utc/ci, endnode/ci-1, grandmaster/1
 │    │               a band per device: its key sections [Details]
 │    ├── low-sat/endnode/ci-1    [Overview] [UTC]
 │    └── low-sat/grandmaster/1   [Overview] [UTC]
 └── low-sat/meridianclock/1      [Overview]
```

**Subsystems** come from device references: a property whose value is
another device's TRL (`SatUtcTrl`, `SubServerTrls`, …) makes a parent → child
link. Every device with children gets a page holding itself and its children,
except an "umbrella" device whose children all have pages of their own (the
controller above), which goes on the overview. Define them yourself with
`[[tool.ska-taranta-setup.subsystems]]` (see below).

**Look and layout.** Pages fill the screen width: Taranta places widgets at
fixed pixel positions, so the layout is computed for `screen_width_px`
(1920 by default; set your own, or pass `generate --screen-width`). Every
page starts with the standard **SKAO banner**: the
circular SKAO logo and the page title on SKAO Blueshift Navy, the navigation
buttons, and the Redshift Magenta-to-navy brand stripe beneath. Colours and
the heading font (Noto Sans, where installed) follow the SKA brand guidelines
as used by `ska-ser-sphinx-theme`. Every page sits on one grid (`columns` columns of
`section_width_px`), centred on the screen. Each section is a box with a
coloured heading strip and a tint of the same colour: Device navy, Status
green, Measurements purple, Settings amber, Commands indigo, Expert grey,
name families slate. Sections fill rows left to right, and the boxes in a
row are stretched to the same height so they line up. Device and Status come
first and Settings, Commands and Expert last; the rest are ordered by height,
so sections sharing a row are of similar size. Trend plots go in a **Trends**
area at the bottom of each device page, each two columns wide, rather than
being squeezed into their sections (except for repeated blocks like ports,
whose values and dials stay in their sections).

On a subsystem page each device shows a **summary** by default: its state,
health, status and mode controls, plus its most indicator-heavy sections
(repeated blocks like the grandmaster's 16 ports stay on its own page). Set
`subsystem_detail = "full"` to show everything.

Links are Taranta `DASHLINK` buttons, which find their target **by name** in
your dashboard library. So import or upload all the pages together, and keep
the generated names. Re-importing in the UI an existing name gets ` copy 1`
appended and breaks the links, whereas `ska-taranta upload` updates in place.

### 4. Which widget for which attribute?

| Attribute | Widget |
| --- | --- |
| `State` | Device status (LED and name) |
| `healthState` | LED, green when OK |
| enum with ok/error-like labels | LED, green on the "good" label |
| other read-only enum | value display with enum labels |
| writable enum (`adminMode`, `controlMode`, …) | dropdown writer |
| boolean | LED (red when true for `*fault*`/`*error*`/`*alarm*`) |
| writable boolean | switch |
| bounded physical quantity (temperature, voltage, power, load, pressure, wind speed, direction, …; from name or unit) | dial, and a trend plot |
| other physical or timing quantity (offsets, delays, …) | value display, and a trend plot |
| raw signals behind a measurement (`…ADC`, `…Raw`, counts, a `…Current` 4–20 mA loop) | value display (no dial or plot) |
| counters, identifiers, other numbers and strings | value display |
| writable number or string | writer |
| numeric array | spectrum plot |
| numeric arrays plus an X-axis array (`xValues`, `frequencies`, …) | one chart of the arrays against it, full width in Trends (e.g. an RFI monitor's traces against frequency) |
| other array | value display (JSON) |
| `healthInfo` | logger |
| commands (not `State`/`Status`) | command button (`Init`, to re-initialise the device, first); "testing" and `EXPERT` ones go to an Expert section |

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
tile_size = 10                         # Taranta's MIN_WIDGET_SIZE: 10 in the SKA image, 20 upstream
taranta_user = "user1"                 # account `upload` logs in as
helmfile_environment = "minikube-ci"
helmfile_env = { K8S_DEPLOY_WR_SIMULATOR = "true" }
exclude_classes = [".*Simulator$"]     # regexes
exclude_devices = []                   # regexes on TRL
interface_key_properties = ["Model"]
tango_db = "taranta"                   # URL segment before /taranta: /<ns>/<tango_db>/...
startup_timeout = 20.0
# C++ servers: where to find built executables, and how long to wait for them
# (a server run from its container image may first need pulling).
cpp_build_dirs = ["build", "cmake-build-*", "out/build"]
cpp_startup_timeout = 90.0
container_startup_timeout = 240.0
# Explicit devices, instead of reading helm:
# devices = { MyDevice = ["my/device/1", "my/device/2"] }

# Extra properties for starting a class locally:
[tool.ska-taranta-setup.properties.MyDevice]
SomeMandatoryProperty = "value"
```

`[tool.tangodifdoc.<Class>.properties]` is also read, so a project already
documented with `tangodocgen --auto` needs no extra configuration. Those values
never override the `interface_key_properties` of the deployed device.

## Generation options

Everything below is optional; misspelt options are reported as errors.

```toml
[tool.ska-taranta-setup.generate]
device_dashboards = true        # a detailed page per device
subsystems = "auto"             # "auto" (from device references) or "none"
subsystem_detail = "summary"    # or "full": every section of every device
summary_sections = 3            # sections per device on summary pages
status_attributes = ["healthState"]  # always in status bars, after State
headline_status = 3             # extra status LEDs picked per device
status_bar_columns = 6          # devices per status-bar row
expert = true                   # include EXPERT attributes/commands
plots = true                    # trend plots for physical quantities
max_plots_per_section = 2
dials = true                    # dials for bounded quantities
exclude_attributes = ["loggingLevel"]   # every class; regexes, any case
exclude_commands = ["GetVersionInfo"]

# Leave attributes / commands out for one device class only. See what each
# class has, and where it goes, with `ska-taranta attributes`.
[tool.ska-taranta-setup.generate.exclude_attributes_by_class]
SatWhiteRabbit = ["net_wr1[0-5]_.*", "hdd.*"]

[tool.ska-taranta-setup.generate.exclude_commands_by_class]
SatUtc = ["ForceRollup"]

# Force a widget for matching attributes (first match wins). Kinds: led,
# display, dial, plot, writer, dropdown, switch, logger, spectrum, hide.
[tool.ska-taranta-setup.generate.widgets]
"pwsl_temp|pwsr_temp" = "display"
"healthInfo" = "display"
"tsrc[12]_message" = "hide"

# Hand-defined subsystem pages (replace the automatic ones). Devices are TRL
# regexes in display order; the first match is the subsystem's root.
[[tool.ska-taranta-setup.subsystems]]
name = "Timing"
devices = ["low-sat/utc/.*", "low-sat/grandmaster/.*", "low-sat/endnode/.*"]

[[tool.ska-taranta-setup.subsystems]]
name = "Clocks"
devices = ["low-sat/meridianclock/.*", "low-sat/nettimeclock/.*"]
detail = "full"

# Sizes, in pixels.
[tool.ska-taranta-setup.layout]
screen_width_px = 1920          # the screen to fill; set to yours (e.g. 2560)
columns = 0                     # 0: as many as fit the screen (min 400px each)
section_width_px = 0            # 0: stretch the columns to fill the screen
row_px = 38
gap_px = 24
dials_per_row = 2               # gauges per row, each captioned with name and unit
dial_px = 180                   # gauge size when two share a row
dial_alone_px = 220             # gauge size on its own
plot_px = 320                   # trend plot height
plot_span = 2                   # grid columns per trend plot
inset_px = 10                   # padding inside each box
heading_px = 32                 # section heading strip height
heading_gap_px = 8              # space under a heading strip
header_gap_px = 20              # space under title bars
menu_max_items = 5              # dropdown menu items to make room for
banner = true                   # the SKAO banner on every page
logo = ""                       # banner logo: path to an SVG/PNG (default: SKAO mark)
logo_px = 34
```

The bundled logo is the circular SKAO mark from `ska-ser-sphinx-theme`
(BSD-3-Clause, SKAO). It's 32 px; for a sharper one, point `logo` at an SVG.

### Choosing which attributes to show

Everything is shown to begin with. To trim a dashboard:

1. `uv run ska-taranta attributes -d <device or class>` lists each attribute,
   its section and widget(s).
2. Add the ones you don't want, as regexes, to
   `exclude_attributes_by_class` (one class) or `exclude_attributes` (all).
   To change a widget rather than hide it, use `generate.widgets`
   (e.g. `"pwsl_temp" = "display"`, or `"hide"`).
3. `uv run ska-taranta generate && make taranta-upload`.

## Development

```bash
git clone https://github.com/davejwalsh/ska-taranta-setup.git && cd ska-taranta-setup
uv sync
uv run pytest --doctest-modules src tests
uv run ruff check src tests && uv run ruff format --check src tests
```

To try your changes in a real project, install your checkout there in
editable mode, so every `ska-taranta` run uses your working copy:

```bash
uv add --dev --editable ../ska-taranta-setup
```

That writes a local path into the project's `pyproject.toml`, which CI can't
resolve, so switch back to the Git URL (see [Installing](#installing))
before merging. For C++ projects, `uv tool install --editable
../ska-taranta-setup --with pytango` does the same.

`tests/fixtures/sat-lmc-devices.json` is a real snapshot from ska-sat-lmc,
used to test generation end to end.

## Known limitations

* `upload` has been tested against the SKAO shared dashboard service with
  password accounts; `--token` (SSO accounts) follows the same API but hasn't
  been tried with a real SSO token yet.
* The chart versions match the ska-sat-lmc `wom-xxx-taranta` branch
  (Taranta 2.18.9, auth 0.3.1, TangoGQL-ariadne 1.0.13); override them with
  `taranta_version`, `taranta_auth_version` and `tangogql_version`.
* Layout is sized for the grid in `tile_size`. If dashboards look half or
  double size, check `MIN_WIDGET_SIZE` in your Taranta's `config.js`
  (`curl http://localhost:8080/<namespace>/config.js`).
* Taranta's style inputs take one `property: value` per line; one-line
  `a:1;b:2;` CSS is silently ignored. Generated dashboards follow this; keep it
  in mind when hand-editing.
* Very long string values (e.g. `buildState`) wrap and can overlap the next row.
* Taranta clips anything that overflows a widget, dropdown menus included, so
  each writable enum (adminMode, controlMode, …) is a value display plus a
  dropdown tall enough for its menu to open inside. That's the blank space
  under each dropdown.
* Dashboard variables (one dashboard switchable between devices of a class)
  aren't generated yet: you get one dashboard per device.
