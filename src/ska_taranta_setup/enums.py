"""
Find the labels of a command's DevEnum argument in the project's source.

Tango publishes the labels of enum *attributes*, but a command's DevEnum
argument is just "DevEnum": a dashboard can only offer a number box. The
argument's description usually names its enum, though (the RFI monitor's
``AttachDataStream`` takes ``dataStreamType``, an ``enum DataStreamType``),
so look for an enum of that name in the C++ headers or Python modules.
"""

from __future__ import annotations

import re
from pathlib import Path

from ska_taranta_setup.model import Snapshot

CPP_ENUM = re.compile(
    r"\benum\s+(?:class\s+|struct\s+)?(\w+)\s*(?::\s*[\w:\s]+?)?\{([^}]*)\}",
    re.S,
)
PY_ENUM = re.compile(
    r"^class\s+(\w+)\s*\(([^)]*Enum[^)]*)\)\s*:\s*\n((?:[ \t]+.*\n?)+)", re.M
)
SOURCE_SUFFIXES = {".h", ".hh", ".hpp", ".hxx", ".cpp", ".cc", ".cxx", ".py"}
SKIP_DIRS = {".git", ".venv", "build", "node_modules", ".make", "dist"}


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _cpp_members(body: str) -> list[list[object]]:
    body = re.sub(r"//[^\n]*|/\*.*?\*/", "", body, flags=re.S)
    members: list[list[object]] = []
    value = 0
    for raw in body.split(","):
        entry = raw.strip()
        if not entry:
            continue
        name, _, explicit = entry.partition("=")
        if explicit.strip():
            try:
                value = int(explicit.strip(), 0)
            except ValueError:
                return []  # computed values: don't guess
        members.append([name.strip(), value])
        value += 1
    return members


def _py_members(body: str) -> list[list[object]]:
    members: list[list[object]] = []
    for line in body.splitlines():
        match = re.match(r"\s+([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*(#.*)?$", line)
        if match:
            members.append([match.group(1), int(match.group(2))])
    return members


def find_enums(root: Path) -> dict[str, list[list[object]]]:
    """All enums in the project's source, by normalised name."""
    found: dict[str, list[list[object]]] = {}
    for path in sorted(root.rglob("*")):
        if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
            continue
        if any(
            part in SKIP_DIRS or part.startswith(".")
            for part in path.relative_to(root).parts[:-1]
        ):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if path.suffix == ".py":
            for name, _, body in PY_ENUM.findall(text):
                members = _py_members(body)
                if members:
                    found.setdefault(_key(name), members)
        else:
            for name, body in CPP_ENUM.findall(text):
                members = _cpp_members(body)
                if members:
                    found.setdefault(_key(name), members)
    return found


def annotate_command_enums(snapshot: Snapshot, root: Path) -> int:
    """Fill in DevEnum command arguments' choices from source; return how many."""
    commands = [
        cmd
        for interface in snapshot.interfaces.values()
        for cmd in interface.commands
        if cmd.in_type == "DevEnum" and not cmd.in_enum and cmd.doc_in.strip()
    ]
    if not commands:
        return 0
    enums = find_enums(root)
    count = 0
    for cmd in commands:
        members = enums.get(_key(cmd.doc_in.split()[0]))
        if members:
            cmd.in_enum = members
            count += 1
    return count
