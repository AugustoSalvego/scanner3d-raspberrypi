"""Configuration validation, safe path resolution and scan planning."""

from __future__ import annotations

import json

import pytest

from scanner_core import paths
from scanner_core.config import (
    CameraConfig,
    DetectorConfig,
    LaserConfig,
    LaserControlMode,
    MotorConfig,
    ScanConfig,
    ScannerConfig,
    ScannerMode,
)
from scanner_core.errors import ConfigurationError, UnsafePathError
from scanner_core.scan_plan import closing_steps, plan_capture_positions


class TestScannerConfig:
    def test_defaults_are_valid(self):
        assert ScannerConfig().validate().mode is ScannerMode.SIMULATION

    def test_round_trip_through_dict(self):
        config = ScannerConfig().validate()
        restored = ScannerConfig.from_dict(config.to_dict())

        assert restored.to_dict() == config.to_dict()

    def test_is_json_serialisable(self):
        json.dumps(ScannerConfig().validate().to_dict())

    def test_unknown_setting_is_rejected(self):
        with pytest.raises(ConfigurationError):
            ScannerConfig.from_dict({"scan": {"not_a_setting": 1}})

    def test_unknown_mode_is_rejected(self):
        with pytest.raises(ConfigurationError):
            ScannerConfig.from_dict({"mode": "telepathy"})

    def test_copy_is_independent(self):
        config = ScannerConfig().validate()
        clone = config.copy()

        clone.scan.capture_count = 999

        assert config.scan.capture_count != 999


class TestSectionValidation:
    def test_motor_pins_must_be_unique(self):
        config = MotorConfig(pins=(17, 17, 22, 23))

        with pytest.raises(ConfigurationError):
            config.validate()

    def test_motor_direction_must_be_plus_or_minus_one(self):
        config = MotorConfig(direction=2)

        with pytest.raises(ConfigurationError):
            config.validate()

    def test_camera_fourcc_must_be_four_characters(self):
        config = CameraConfig(fourcc="MJP")

        with pytest.raises(ConfigurationError):
            config.validate()

    def test_camera_fourcc_is_upper_cased(self):
        config = CameraConfig(fourcc="mjpg")
        config.validate()

        assert config.fourcc == "MJPG"

    def test_scan_capture_count_has_a_floor(self):
        config = ScanConfig(capture_count=1)

        with pytest.raises(ConfigurationError):
            config.validate()

    def test_detector_hue_band_order_is_checked(self):
        config = DetectorConfig()
        config.hue_low_min = 20
        config.hue_low_max = 5

        with pytest.raises(ConfigurationError):
            config.validate()

    def test_gpio_laser_requires_a_pin(self):
        """The software must never invent a laser pin."""

        config = LaserConfig(control=LaserControlMode.GPIO, gpio_pin=None)

        with pytest.raises(ConfigurationError) as error:
            config.validate()

        assert "never guesses" in str(error.value)

    def test_external_laser_is_not_software_controlled(self):
        config = LaserConfig()
        config.validate()

        assert not config.is_software_controlled

    def test_laser_off_frames_require_a_switchable_laser(self):
        config = ScannerConfig()
        config.scan.capture_laser_off_frames = True

        with pytest.raises(ConfigurationError) as error:
            config.validate()

        assert "laser.control" in str(error.value)

    def test_laser_off_frames_allowed_with_gpio(self):
        config = ScannerConfig()
        config.laser.control = LaserControlMode.GPIO
        config.laser.gpio_pin = 24
        config.scan.capture_laser_off_frames = True

        assert config.validate() is config


class TestConfigStore:
    def test_persists_and_reloads(self, config_store):
        config_store.update({"scan": {"capture_count": 77}})

        reloaded = config_store.load()

        assert reloaded.scan.capture_count == 77

    def test_rejected_patch_leaves_the_config_untouched(self, config_store):
        before = config_store.get().scan.capture_count

        with pytest.raises(ConfigurationError):
            config_store.update({"scan": {"capture_count": -5}})

        assert config_store.get().scan.capture_count == before

    def test_partial_patch_keeps_other_sections(self, config_store):
        config_store.update({"scan": {"capture_count": 33}})
        config_store.update({"motor": {"step_delay": 0.004}})

        config = config_store.get()

        assert config.scan.capture_count == 33
        assert config.motor.step_delay == 0.004

    def test_get_returns_an_isolated_copy(self, config_store):
        first = config_store.get()
        first.scan.capture_count = 1234

        assert config_store.get().scan.capture_count != 1234

    def test_set_mode(self, config_store):
        assert config_store.set_mode("physical").mode is ScannerMode.PHYSICAL

    def test_a_corrupt_file_does_not_stop_the_scanner(self, config_store):
        config_store.save()
        config_store.path.write_text("{ not json", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            config_store.load()

        # Defaults were restored, so the scanner still runs.
        assert config_store.get().mode is ScannerMode.SIMULATION


class TestSafePaths:
    @pytest.mark.parametrize(
        "name",
        [
            "..",
            ".",
            "",
            "../secret.ply",
            "..\\secret.ply",
            "/etc/passwd",
            "C:/Windows/system32",
            "C:secret.ply",
            "foo/bar.ply",
            "foo\\bar.ply",
            "with\x00null.ply",
        ],
    )
    def test_rejects_unsafe_components(self, name):
        assert not paths.is_safe_component(name)

    @pytest.mark.parametrize("name", ["cloud.ply", "scan_2026_ab12.ply", "a-b.c~d.jpg"])
    def test_accepts_ordinary_names(self, name):
        assert paths.is_safe_component(name)

    def test_resolve_within_stays_inside_the_root(self, tmp_path):
        resolved = paths.resolve_within(tmp_path, "cloud.ply")

        assert resolved.parent == tmp_path.resolve()

    def test_resolve_within_rejects_traversal(self, tmp_path):
        with pytest.raises(UnsafePathError):
            paths.resolve_within(tmp_path, "../escape.ply")

    def test_resolve_within_enforces_the_suffix(self, tmp_path):
        with pytest.raises(UnsafePathError):
            paths.resolve_within(tmp_path, "cloud.txt", suffix=".ply")

    def test_resolve_within_accepts_the_suffix_case_insensitively(self, tmp_path):
        assert paths.resolve_within(tmp_path, "cloud.PLY", suffix=".ply")

    def test_resolve_within_needs_a_component(self, tmp_path):
        with pytest.raises(UnsafePathError):
            paths.resolve_within(tmp_path)

    def test_nested_components(self, tmp_path):
        resolved = paths.resolve_within(tmp_path, "session", "cloud.ply")

        assert resolved == (tmp_path / "session" / "cloud.ply").resolve()

    def test_outputs_root_follows_the_environment(self, isolated_outputs):
        assert "outputs" in str(isolated_outputs.OUTPUTS_ROOT)
        assert isolated_outputs.SCANS_DIR.exists()


class TestScanPlan:
    def test_spreads_captures_over_one_revolution(self):
        plan = plan_capture_positions(8, 4096)

        assert len(plan) == 8
        assert plan[0].target_step == 0
        assert plan[0].angle_deg == 0.0
        # 360 degrees is never captured: it is the same slice as 0.
        assert plan[-1].angle_deg < 360.0

    def test_targets_are_strictly_increasing(self):
        plan = plan_capture_positions(60, 4096)
        targets = [entry.target_step for entry in plan]

        assert targets == sorted(targets)
        assert len(set(targets)) == len(targets)

    def test_no_rounding_accumulation(self):
        """The classic bug: moving spr/N steps N times misses a full turn."""

        steps_per_revolution = 4096

        for count in (7, 13, 37, 60, 99, 360):
            plan = plan_capture_positions(count, steps_per_revolution)

            # Absolute targets always match the ideal value to within one step.
            for entry in plan:
                ideal = entry.index * steps_per_revolution / count

                assert abs(entry.target_step - ideal) < 1.0

            # The deltas plus the closing move add up to exactly one turn.
            total = sum(entry.delta_steps for entry in plan)
            total += closing_steps(plan, steps_per_revolution)

            assert total == steps_per_revolution

    def test_angle_matches_the_commanded_step(self):
        """Metadata must describe where the platform really was."""

        plan = plan_capture_positions(7, 4096)

        for entry in plan:
            assert entry.angle_deg == pytest.approx(entry.target_step / 4096 * 360.0)

    def test_rejects_too_few_captures(self):
        with pytest.raises(ValueError):
            plan_capture_positions(1, 4096)

    def test_rejects_more_captures_than_steps(self):
        with pytest.raises(ValueError):
            plan_capture_positions(500, 400)

    def test_closing_steps_of_an_empty_plan(self):
        assert closing_steps([], 4096) == 0
