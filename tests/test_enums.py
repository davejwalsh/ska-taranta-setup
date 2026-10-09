"""Tests for DevEnum command arguments."""

from ska_taranta_setup import widgets as w
from ska_taranta_setup.enums import annotate_command_enums, find_enums
from ska_taranta_setup.model import CommandInfo, DeviceInterface, Snapshot


def test_cpp_and_python_enums(tmp_path):
    """C++ enums (with comments and explicit values) and Python enums are found."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "DataStreamType.h").write_text(
        "namespace X {\nenum DataStreamType {\n"
        "    SPECTRUM_CAPTURE_STREAM, // spectra\n    IQ_STREAM\n};\n}\n"
        "enum class Gain : int { LOW = 10, HIGH = 20 };\n"
    )
    (tmp_path / "src" / "modes.py").write_text(
        "import enum\n\n\nclass ObsMode(enum.IntEnum):\n    IDLE = 0\n    SCAN = 3\n"
    )
    enums = find_enums(tmp_path)
    assert enums["datastreamtype"] == [["SPECTRUM_CAPTURE_STREAM", 0], ["IQ_STREAM", 1]]
    assert enums["gain"] == [["LOW", 10], ["HIGH", 20]]
    assert enums["obsmode"] == [["IDLE", 0], ["SCAN", 3]]


def test_command_argument_matched_by_description(tmp_path):
    """A DevEnum argument described as `dataStreamType` gets DataStreamType's labels."""
    (tmp_path / "DataStreamType.h").write_text("enum DataStreamType { A, B };\n")
    cmd = CommandInfo(
        name="AttachDataStream", in_type="DevEnum", doc_in="dataStreamType"
    )
    snapshot = Snapshot(interfaces={"i": DeviceInterface("RFIMonitor", commands=[cmd])})
    assert annotate_command_enums(snapshot, tmp_path) == 1
    assert cmd.in_enum == [["A", 0], ["B", 1]]


def test_enum_command_widget():
    """Taranta can't take DevEnum input, so it's offered as DevShort choices."""
    cmd = CommandInfo(
        name="AttachDataStream",
        in_type="DevEnum",
        doc_in="dataStreamType",
        in_enum=[["SPECTRUM_CAPTURE_STREAM", 0], ["IQ_STREAM", 1]],
    )
    inputs = w.command("d", cmd, "Attach Data Stream")["inputs"]
    assert inputs["command"]["acceptedType"] == "DevShort"
    assert inputs["commandArgs"] == [
        {"name": "Spectrum Capture Stream", "value": "0", "isDefault": True},
        {"name": "IQ Stream", "value": "1", "isDefault": False},
    ]


def test_enum_command_without_labels():
    """With no labels found, it's still usable: a number box with a hint."""
    cmd = CommandInfo(name="SetMode", in_type="DevEnum", doc_in="mode")
    inputs = w.command("d", cmd, "Set Mode")["inputs"]
    assert inputs["command"]["acceptedType"] == "DevShort"
    assert inputs["commandArgs"] == []
    assert inputs["placeholder"] == "intypedesc"
    assert "enter its number" in inputs["command"]["intypedesc"]


def test_other_commands_unchanged():
    """Commands with other argument types keep their own type."""
    cmd = CommandInfo(name="ForceRollup", in_type="DevString")
    inputs = w.command("d", cmd, "Force Rollup")["inputs"]
    assert inputs["command"]["acceptedType"] == "DevString"
    assert inputs["placeholder"] == "intype"
