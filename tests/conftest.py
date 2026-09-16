"""Shared test fixtures.

Every test runs against a temporary output tree, so a test run can never touch
the developer's real scans, calibrations or configuration.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point ``SCANNER_OUTPUTS`` at a temporary directory for every test."""

    from scanner_core import paths

    monkeypatch.setenv("SCANNER_OUTPUTS", str(tmp_path / "outputs"))

    paths.refresh_roots()
    paths.ensure_directories()

    yield paths

    monkeypatch.delenv("SCANNER_OUTPUTS", raising=False)
    paths.refresh_roots()


@pytest.fixture
def simulation_config():
    """A small, fast synthetic rig."""

    from scanner_core.config import SimulationConfig

    config = SimulationConfig(image_width=320, image_height=240, focal_length_px=350.0)
    config.validate()

    return config


@pytest.fixture
def scene(simulation_config):
    from scanner_core.simulation import SyntheticScene

    return SyntheticScene(simulation_config)


@pytest.fixture
def detector():
    from scanner_core.config import DetectorConfig
    from scanner_core.laser_detection import LaserLineDetector

    config = DetectorConfig()
    # The small test rig produces a shorter stripe than a 720p frame would.
    config.min_points = 10
    config.validate()

    return LaserLineDetector(config)


@pytest.fixture
def config_store(tmp_path: Path):
    """A ConfigStore backed by a throwaway file."""

    from scanner_core.config import ConfigStore

    return ConfigStore(path=tmp_path / "scanner_config.json")


@pytest.fixture
def session_manager(tmp_path: Path):
    from scanner_core.session import SessionManager

    return SessionManager(root=tmp_path / "scans")


@pytest.fixture
def calibration_store(tmp_path: Path):
    from scanner_core.calibration.store import CalibrationStore

    return CalibrationStore(directory=tmp_path / "calibration")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "hardware: needs the physical scanner (camera, GPIO). Never runs by default.",
    )
