"""Laser line detector tests."""

from __future__ import annotations

import numpy as np
import pytest

from scanner_core.config import DetectorConfig
from scanner_core.laser_detection import LaserLineDetector, detection_to_diagnostics


def frame_with_stripe(
    width=200,
    height=160,
    column=97.4,
    thickness=2.0,
    background=12,
    intensity=255,
):
    """A dark frame with one red Gaussian stripe at a fractional column."""

    image = np.full((height, width, 3), background, dtype=np.uint8)

    columns = np.arange(width, dtype=np.float64)[None, :]
    profile = np.exp(-0.5 * ((columns - column) / (thickness / 2)) ** 2)
    profile = np.repeat(profile, height, axis=0)

    image[:, :, 2] = np.maximum(image[:, :, 2], profile * intensity)
    image[:, :, 1] = np.maximum(image[:, :, 1], profile * intensity * 0.15)
    image[:, :, 0] = np.maximum(image[:, :, 0], profile * intensity * 0.1)

    return image


def make_detector(**overrides):
    config = DetectorConfig()
    config.min_points = 5

    for key, value in overrides.items():
        setattr(config, key, value)

    config.validate()

    return LaserLineDetector(config)


class TestDetection:
    def test_finds_a_vertical_stripe(self):
        detection = make_detector().detect(frame_with_stripe())

        assert detection.accepted
        assert detection.point_count > 100

    def test_recovers_the_sub_pixel_position(self):
        """The whole point of the weighted centroid.

        The accuracy floor here is the rendering, not the algorithm: the stripe
        is stored as uint8 and its core saturates at 255, so the profile the
        detector sees is already quantised and slightly flat-topped. A quarter
        of a pixel is comfortably inside that floor, and well beyond what
        picking the brightest column could ever achieve.
        """

        for true_column in (60.0, 97.4, 120.75, 150.25):
            detection = make_detector().detect(frame_with_stripe(column=true_column))

            assert detection.accepted

            measured = float(np.median(detection.pixels[:, 0]))

            assert abs(measured - true_column) < 0.25

    def test_beats_whole_pixel_resolution(self):
        """A centroid that just returned the brightest column would fail this."""

        errors = []

        for offset in np.arange(0.0, 1.0, 0.1):
            true_column = 100.0 + offset

            detection = make_detector().detect(frame_with_stripe(column=true_column))
            measured = float(np.median(detection.pixels[:, 0]))

            errors.append(abs(measured - true_column))

            # The measurement must track the fractional position, not snap to
            # the nearest integer column.
            assert abs(measured - round(true_column)) > 0.0 or offset == 0.0

        assert float(np.mean(errors)) < 0.15

    def test_handles_the_red_hue_wraparound(self):
        """Red spans both ends of the hue scale; both must be caught."""

        # Pure red sits at hue 0; a slightly magenta laser wraps to ~175.
        image = np.full((120, 160, 3), 10, dtype=np.uint8)
        image[:, 78:82, 2] = 240
        image[:, 78:82, 0] = 40  # push the hue towards the 170-179 band

        detection = make_detector().detect(image)

        assert detection.accepted
        assert 77.0 < float(np.median(detection.pixels[:, 0])) < 82.0

    def test_rejects_a_frame_without_a_laser(self):
        blank = np.full((120, 160, 3), 10, dtype=np.uint8)

        detection = make_detector().detect(blank)

        assert not detection.accepted
        assert detection.point_count == 0
        assert "threshold" in detection.reason or "centroid" in detection.reason

    def test_rejects_a_green_line(self):
        """Only the red laser counts; room lighting must not be mistaken for it."""

        image = np.full((120, 160, 3), 10, dtype=np.uint8)
        image[:, 78:82, 1] = 250

        detection = make_detector().detect(image)

        assert not detection.accepted

    def test_ignores_a_weaker_second_stripe(self):
        """The laser on the platform must not drag the centroid off the object."""

        image = frame_with_stripe(column=60.0)
        decoy = frame_with_stripe(column=150.0, intensity=90)

        combined = np.maximum(image, decoy)

        detection = make_detector().detect(combined)

        assert detection.accepted

        measured = float(np.median(detection.pixels[:, 0]))

        assert abs(measured - 60.0) < 0.5

    def test_reports_thickness(self):
        thin = make_detector().detect(frame_with_stripe(thickness=1.5))
        thick = make_detector().detect(frame_with_stripe(thickness=6.0))

        assert float(np.mean(thin.thickness)) < float(np.mean(thick.thickness))

    def test_confidence_is_bounded(self):
        detection = make_detector().detect(frame_with_stripe())

        assert detection.confidence.min() >= 0.0
        assert detection.confidence.max() <= 1.0

    def test_rejects_a_frame_that_is_not_colour(self):
        detection = make_detector().detect(np.zeros((40, 40), dtype=np.uint8))

        assert not detection.accepted

    def test_rejects_an_empty_frame(self):
        detection = make_detector().detect(np.zeros((0, 0, 3), dtype=np.uint8))

        assert not detection.accepted


class TestRegionOfInterest:
    def test_roi_limits_the_search_and_offsets_coordinates(self):
        image = frame_with_stripe(width=200, column=150.0)

        detector = make_detector(roi=(0.5, 0.0, 0.5, 1.0))
        detection = detector.detect(image)

        assert detection.accepted
        # Coordinates must come back in full-frame pixels, not ROI pixels.
        assert abs(float(np.median(detection.pixels[:, 0])) - 150.0) < 0.5

    def test_roi_excludes_a_stripe_outside_it(self):
        image = frame_with_stripe(width=200, column=30.0)

        detection = make_detector(roi=(0.5, 0.0, 0.5, 1.0)).detect(image)

        assert not detection.accepted

    def test_roi_rect_stays_inside_the_image(self):
        detector = make_detector(roi=(0.9, 0.9, 0.1, 0.1))

        x, y, width, height = detector.roi_rect(160, 200)

        assert x + width <= 200
        assert y + height <= 160


class TestScanAxis:
    def test_columns_mode_finds_a_horizontal_stripe(self):
        vertical = frame_with_stripe(width=200, height=160, column=97.4)
        horizontal = np.transpose(vertical, (1, 0, 2)).copy()

        detection = make_detector(scan_axis="columns").detect(horizontal)

        assert detection.accepted
        # The stripe is now a row, so it is the v coordinate that is constant.
        assert abs(float(np.median(detection.pixels[:, 1])) - 97.4) < 0.3


class TestDifferenceMethod:
    def test_cancels_a_bright_background(self):
        """Differencing is what makes detection work in a lit room."""

        clutter = np.zeros((160, 200, 3), dtype=np.uint8)
        clutter[:, 20:60, 2] = 180  # a large red object in the scene

        laser_off = clutter
        laser_on = np.maximum(clutter, frame_with_stripe(column=140.0, background=0))

        hsv_detector = make_detector()
        difference_detector = make_detector(method="difference")

        hsv = hsv_detector.detect(laser_on)
        difference = difference_detector.detect(laser_on, laser_off)

        assert difference.accepted
        assert abs(float(np.median(difference.pixels[:, 0])) - 140.0) < 0.5

        # The plain HSV pass is pulled towards the red clutter; differencing
        # is immune to it.
        if hsv.accepted:
            assert abs(float(np.median(hsv.pixels[:, 0])) - 140.0) >= abs(
                float(np.median(difference.pixels[:, 0])) - 140.0
            )

    def test_requires_a_laser_off_frame(self):
        detection = make_detector(method="difference").detect(frame_with_stripe())

        assert not detection.accepted
        assert "laser-off" in detection.reason

    def test_mismatched_frame_sizes_are_rejected(self):
        detection = make_detector(method="difference").detect(
            frame_with_stripe(), np.zeros((10, 10, 3), dtype=np.uint8)
        )

        assert not detection.accepted


def frame_under_green_light(width=200, height=160, column=97.4, seed=3):
    """A red laser on white paper in a box lit by green LEDs, as photographed.

    The paper is cyan-ish and dark in red. The laser core saturates every
    channel to near-white, so it is not "red" for a hue test any more, but it
    still peaks at 255 in the red channel. The glow into the paper is stronger
    on one side, as it was on the real board.
    """

    rng = np.random.default_rng(seed)
    x = np.arange(width, dtype=np.float64)[None, :]
    core = np.exp(-0.5 * ((x - column) / 1.6) ** 2)
    glow = np.where(x > column, np.exp(-(x - column) / 6.0), 0.0)

    red = 70 + 185 * core + 55 * glow
    green = 190 + 70 * np.exp(-0.5 * ((x - column) / 1.1) ** 2)
    blue = 165 + 70 * np.exp(-0.5 * ((x - column) / 1.0) ** 2)

    image = np.empty((height, width, 3), dtype=np.float64)
    image[:, :, 0] = blue
    image[:, :, 1] = green
    image[:, :, 2] = red
    image += rng.normal(0.0, 2.0, image.shape)

    return np.clip(np.rint(image), 0, 255).astype(np.uint8)


class TestRedChannelMethod:
    def test_finds_a_laser_that_looks_white_under_green_light(self):
        """What the real scanner showed: the HSV test misses most of this line.

        The tolerance is not the detector's: the glow adds to one side of the
        core, so the clipped (255) top of the line is itself up to about half a
        pixel off the true centre, and no threshold can see past the image.
        """

        for true_column in (60.0, 97.4, 140.6):
            image = frame_under_green_light(column=true_column)
            detection = make_detector(method="red_channel").detect(image)

            assert detection.accepted
            assert detection.point_count > 100
            assert abs(float(np.median(detection.pixels[:, 0])) - true_column) < 0.75

    def test_the_hsv_method_does_not_find_it(self):
        detection = make_detector().detect(frame_under_green_light())

        assert not detection.accepted or detection.point_count < 20

    def test_the_glow_does_not_pull_the_centre(self):
        """Keeping the whole glow would move the centre towards its bright side;
        keeping only the top of the peak (the default ratio) does not."""

        for true_column in (60.0, 97.4, 140.6):
            image = frame_under_green_light(column=true_column)

            tight = make_detector(method="red_channel").detect(image)
            loose = make_detector(method="red_channel", red_min=80, red_peak_ratio=0.3).detect(image)

            assert abs(float(np.median(tight.pixels[:, 0])) - true_column) < 0.75
            assert abs(float(np.median(loose.pixels[:, 0])) - true_column) > 1.5

    def test_rejects_a_frame_without_a_laser(self):
        image = frame_under_green_light()
        image[:, :, 2] = 70

        detection = make_detector(method="red_channel").detect(image)

        assert not detection.accepted

    def test_reports_its_method(self):
        detection = make_detector(method="red_channel").detect(frame_under_green_light())

        assert detection.statistics["method"] == "red_channel"


class TestDiagnostics:
    def test_debug_images_are_full_frame(self):
        image = frame_with_stripe()

        detector = make_detector(roi=(0.25, 0.0, 0.5, 1.0))
        detection = detector.detect(image, keep_debug_images=True)

        assert detection.mask is not None
        assert detection.mask.shape == image.shape[:2]
        assert detection.response.shape == image.shape[:2]

    def test_bundle_contains_the_expected_images(self):
        image = frame_with_stripe()
        detector = make_detector()
        detection = detector.detect(image, keep_debug_images=True)

        images = detection_to_diagnostics(detector, image, detection)

        assert {"original", "mask", "response", "overlay"} <= set(images)
        assert images["overlay"].shape == image.shape


class TestConfigurationValidation:
    def test_rejects_an_even_blur_kernel(self):
        config = DetectorConfig()
        config.blur_kernel = 4

        with pytest.raises(Exception):
            config.validate()

    def test_rejects_an_roi_outside_the_image(self):
        config = DetectorConfig()
        config.roi = (0.8, 0.0, 0.5, 1.0)

        with pytest.raises(Exception):
            config.validate()

    def test_rejects_an_unknown_method(self):
        config = DetectorConfig()
        config.method = "infrared"

        with pytest.raises(Exception):
            config.validate()

    def test_rejects_a_red_peak_ratio_out_of_range(self):
        for value in (0.1, 1.5):
            config = DetectorConfig()
            config.method = "red_channel"
            config.red_peak_ratio = value

            with pytest.raises(Exception):
                config.validate()
