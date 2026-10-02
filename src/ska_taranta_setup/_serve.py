"""
Start a single Tango device class as a device server.

Usage: ``python -m ska_taranta_setup._serve <module> <ClassName> <tango args...>``

Used by :mod:`ska_taranta_setup.introspect` so that a device can be started
without the host project having to provide a per-class entry point.
"""

import importlib
import sys


def main() -> None:
    """Import the requested class and run it."""
    from tango.server import run

    module_name, class_name, *tango_args = sys.argv[1:]
    device_class = getattr(importlib.import_module(module_name), class_name)
    run((device_class,), args=[class_name, *tango_args])


if __name__ == "__main__":
    main()
