"""Tests for widget choice and grouping."""

import pytest

from ska_taranta_setup.heuristics import (
    Section,
    device_sections,
    dial_range,
    family_key,
    place_attribute,
    quantity,
    short_label,
    status_compare,
    trend_plots,
)
from ska_taranta_setup.model import (
    AttributeInfo,
    CommandInfo,
    DeviceInterface,
    prettify,
)

DEV = "taranta://a/b/c"


def widget_for(**kwargs):
    """Place one attribute in an empty section and return what was added."""
    section = Section("s")
    place_attribute(section, DEV, AttributeInfo(**kwargs))
    return section


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"name": "healthState", "dtype": "DevEnum"}, "LED_DISPLAY"),
        ({"name": "healthInfo", "dformat": "SPECTRUM"}, "ATTRIBUTE_LOGGER"),
        (
            {
                "name": "adminMode",
                "dtype": "DevEnum",
                "writable": "READ_WRITE",
                "enum_labels": ["ONLINE", "OFFLINE"],
            },
            "ATTRIBUTE WRITER DROPDOWN",
        ),
        (
            {
                "name": "general_status",
                "dtype": "DevEnum",
                "enum_labels": ["_SNMPEnum_INVALID_0", "ok", "warning", "critical"],
            },
            "LED_DISPLAY",
        ),
        (
            {
                "name": "tsrc1_role",
                "dtype": "DevEnum",
                "enum_labels": ["disabled", "survey", "passive", "active"],
            },
            "ATTRIBUTE_DISPLAY",
        ),
        ({"name": "sfp_detect", "dtype": "DevBoolean"}, "LED_DISPLAY"),
        (
            {"name": "enabled", "dtype": "DevBoolean", "writable": "READ_WRITE"},
            "BOOLEAN_DISPLAY",
        ),
        ({"name": "hostname"}, "ATTRIBUTE_DISPLAY"),
        ({"name": "target", "writable": "READ_WRITE"}, "ATTRIBUTE_WRITER"),
        (
            {"name": "gain", "dtype": "DevDouble", "writable": "READ_WRITE"},
            "ATTRIBUTE_WRITER",
        ),
        ({"name": "rx_packets", "dtype": "DevLong64"}, "ATTRIBUTE_DISPLAY"),
        ({"name": "bandpass", "dtype": "DevDouble", "dformat": "SPECTRUM"}, "SPECTRUM"),
        ({"name": "names", "dformat": "SPECTRUM"}, "ATTRIBUTE_DISPLAY"),
    ],
)
def test_widget_choice(kwargs, expected):
    """Each kind of attribute gets the expected widget."""
    section = widget_for(**kwargs)
    # Writable enums are a value display then the dropdown; check the last.
    assert [w["type"] for w in section.widgets][-1:] == [expected]


def test_bounded_temperature_gets_dial_and_trend():
    """A temperature with alarm limits is a dial, and is also plotted."""
    section = widget_for(
        name="pwsl_temp", dtype="DevLong64", min_alarm=-5, max_alarm=50
    )
    assert section.widgets == []
    assert [d["type"] for d in section.dials] == ["ATTRIBUTE_DIAL"]
    assert section.dials[0]["inputs"]["max"] == 60
    assert list(section.trends) == ["Temperature"]


def test_unbounded_voltage_is_plotted_not_dialled():
    """Without limits there's no sensible dial range: show the value, plot it."""
    section = widget_for(name="pwsl_vin", dtype="DevDouble")
    assert [w["type"] for w in section.widgets] == ["ATTRIBUTE_DISPLAY"]
    assert list(section.trends) == ["Voltage"]


def test_fault_boolean_is_red_when_true():
    """For a fault flag, true is bad."""
    led = widget_for(name="psu_fault", dtype="DevBoolean").widgets[0]["inputs"]
    assert (led["compare"], led["trueColor"]) == ("true", "#e0524f")


def test_status_compare_uses_good_label_index():
    """The LED compares against the index of the first 'good' label."""
    attr = AttributeInfo(
        name="x_status", dtype="DevEnum", enum_labels=["disabled", "ok"]
    )
    assert status_compare(attr) == 1


def test_counters_are_not_physical_quantities():
    """Packet counters are never plotted, even if their name mentions power."""
    assert quantity(AttributeInfo(name="power_rx_pkts", dtype="DevLong64")) is None
    assert (
        quantity(AttributeInfo(name="board_temp", dtype="DevDouble")) == "Temperature"
    )
    assert quantity(AttributeInfo(name="x", dtype="DevDouble", unit="V")) == "Voltage"


def test_dial_range_ignores_integer_type_limits():
    """Limits that are just the int32 range aren't real limits."""
    attr = AttributeInfo(name="t", min_value=-(2**31), max_value=2**31 - 1)
    assert dial_range(attr, "Temperature") is None
    assert dial_range(AttributeInfo(name="cpu_usage"), "Load") == (0.0, 100.0)


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("pwsl_temp", "pwsl"),
        ("tsrc1_status", "tsrc1"),
        ("net_wr12_sfp_temp", "net_wr12"),
        ("gpsChannelTrkStat3", "gps"),
        ("healthState", "health"),
    ],
)
def test_family_key(name, key):
    """Families split repeated, indexed blocks but not trailing indices."""
    assert family_key(name) == key


def test_short_label_strips_family_prefix():
    """Inside the PWSL section, 'pwsl_vin' is just 'Vin'."""
    assert short_label(AttributeInfo(name="pwsl_vin"), "pwsl") == "Vin"
    assert (
        short_label(AttributeInfo(name="pwsl_vin", label="Input V"), "pwsl")
        == "Input V"
    )


def test_prettify():
    """Acronyms are upper-cased and indices stay attached."""
    assert prettify("net_wr0_ipv4_addr") == "Net WR0 IPV4 Addr"


def test_device_sections_groups_families_and_commands():
    """SKA attributes go first, families get sections, commands their own."""
    interface = DeviceInterface(
        class_name="X",
        attributes=[
            AttributeInfo(name="State", dtype="DevState"),
            AttributeInfo(name="healthState", dtype="DevEnum"),
            *(
                AttributeInfo(name=f"psu_{n}", dtype="DevDouble")
                for n in ("vin", "vout", "temp")
            ),
            AttributeInfo(name="serial_number"),
            AttributeInfo(name="debug_thing", disp_level="EXPERT"),
        ],
        commands=[
            CommandInfo(name="Init"),
            CommandInfo(name="Reset"),
            CommandInfo(name="Poke", in_type="DevString", doc_in="For testing only"),
        ],
    )
    sections = device_sections(DEV, interface)
    assert [s.title for s in sections] == [
        "Device",
        "PSU",
        "Information",
        "Commands",
        "Expert",
    ]
    device = sections[0]
    assert [w["type"] for w in device.widgets] == ["DEVICE_STATUS", "LED_DISPLAY"]
    commands = sections[3].widgets
    assert [c["inputs"]["command"]["command"] for c in commands] == ["Reset"]
    assert {w["inputs"].get("buttonText") for w in sections[4].widgets} >= {"Poke"}


def test_plots_capped_and_dial_free_first():
    """At most two plots per section, preferring quantities without dials."""
    section = Section("s")
    for name, extra in [
        ("a_temp", {"max_alarm": 50}),
        ("a_vin", {}),
        ("a_power_in", {}),
        ("a_offset", {}),
    ]:
        place_attribute(
            section, DEV, AttributeInfo(name=name, dtype="DevDouble", **extra)
        )
    plots = trend_plots(section, DEV)
    assert len(plots) == 2
    plotted = {
        a["attribute"]["attribute"] for p in plots for a in p["inputs"]["attributes"]
    }
    assert "a_temp" not in plotted


def test_real_interfaces_produce_sections(sat_lmc_snapshot):
    """Every discovered sat-lmc interface yields a Device section first."""
    for interface in sat_lmc_snapshot.interfaces.values():
        sections = device_sections(DEV, interface)
        assert sections[0].title == "Device"
        assert sections[0].widgets[0]["type"] == "DEVICE_STATUS"


def test_enum_setter_shows_value_and_has_room_for_its_menu():
    """
    Writable enums: a display, then a dropdown with room for its menu.

    The dropdown doesn't show the current value, and Taranta clips its menu
    to the widget, so it comes after a display and reserves a row per item.
    """
    section = widget_for(
        name="controlMode",
        dtype="DevEnum",
        writable="READ_WRITE",
        enum_labels=[
            "NO_MONITOR_NO_CONTROL",
            "MONITOR_NO_CONTROL",
            "MONITOR_AND_CONTROL",
        ],
    )
    display, setter = section.widgets
    assert display["type"] == "ATTRIBUTE_DISPLAY"
    assert setter["inputs"]["writeValues"] == [
        {"title": "No Monitor No Control", "value": "0"},
        {"title": "Monitor No Control", "value": "1"},
        {"title": "Monitor And Control", "value": "2"},
    ]
    assert setter["_menu_rows"] == 3
