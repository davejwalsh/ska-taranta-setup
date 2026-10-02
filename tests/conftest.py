"""Shared fixtures."""

from pathlib import Path

import pytest

from ska_taranta_setup.model import Snapshot

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sat_lmc_snapshot() -> Snapshot:
    """Interfaces discovered from ska-sat-lmc (main, minikube-ci, simulators on)."""
    return Snapshot.load(FIXTURES / "sat-lmc-devices.json")
