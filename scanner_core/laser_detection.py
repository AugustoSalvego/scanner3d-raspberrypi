"""Red laser line detection with sub-pixel accuracy.

Two detection methods are provided:

``hsv``
    Threshold the frame in HSV using *two* hue bands, because red wraps around
    the end of OpenCV's 0-179 hue scale (0-10 and 170-179).  This is the method
    that works with a permanently powered laser and it is the default.

``difference``
    Subtract a laser-off frame from a laser-on frame.  Ambient light cancels
    out, so this is dramatically more robust - but it needs a laser that the
    software can switch, which the reference build does not have.  It is opt-in
    and requires ``laser.control = 'gpio'``.

Both methods share the same sub-pixel extraction: for every image row the
strongest laser response is located, a window is centred on that peak, and the
intensity-weighted centroid inside the window gives a fractional column.  The
whole pipeline is NumPy/OpenCV vectorised - no per-pixel Python loops, which
matters on a Raspberry Pi 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from scanner_core.config import DetectorConfig


@dataclass
class LaserDetection:
    """Result of running the detector on one frame."""

    #: ``(N, 2)`` sub-pixel ``(u, v)`` coordinates in full-frame pixels.
    pixels: np.ndarray
    #: ``(N,)`` relative confidence in ``[0, 1]``.
    confidence: np.ndarray
    #: ``(N,)`` measured laser line thickness in pixels.
    thickness: np.ndarray
    accepted: bool
    reason: str
    statistics: dict[str, Any] = field(default_factory=dict)
    #: Binary mask of the laser, full frame size.  Kept for diagnostics only.
    mask: np.ndarray | None = None
    #: Laser response image used for the weighted centroid.
    response: np.ndarray | None = None

    @property
    def point_count(self) -> int:
        return int(len(self.pixels))

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "point_count": self.point_count,
            "mean_confidence": float(np.mean(self.confidence)) if self.point_count else 0.0,
            "mean_thickness_px": float(np.mean(self.thickness)) if self.point_count else 0.0,
            **self.statistics,
        }


def _empty_detection(reason: str, statistics: dict[str, Any] | None = None) -> LaserDetection:
    return LaserDetection(
        pixels=np.zeros((0, 2), dtype=np.float64),
        confidence=np.zeros(0, dtype=np.float64),
        thickness=np.zeros(0, dtype=np.float64),
        accepted=False,
        reason=reason,
        statistics=statistics or {},
    )


class LaserLineDetector:
    """Extract the laser stripe from a frame."""

    def __init__(self, config: DetectorConfig | None = None) -> None:
        self.config = config or DetectorConfig()
        self.config.validate()

    # ------------------------------------------------------------------
    # Region of interest
    # ------------------------------------------------------------------
    def roi_rect(self, height: int, width: int) -> tuple[int, int, int, int]:
        """Pixel rectangle ``(x, y, w, h)`` for the configured fractional ROI."""

        fx, fy, fw, fh = self.config.roi

        x = int(round(fx * width))
        y = int(round(fy * height))
        w = max(1, int(round(fw * width)))
        h = max(1, int(round(fh * height)))

        x = min(x, width - 1)
        y = min(y, height - 1)
        w = min(w, width - x)
        h = min(h, height - y)

        return x, y, w, h

    # ------------------------------------------------------------------
    # Response images
    # ------------------------------------------------------------------
    def _hsv_mask(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(mask, response)`` for the HSV method."""

        config = self.config

        working = frame_bgr

        if config.blur_kernel >= 3:
            working = cv2.GaussianBlur(working, (config.blur_kernel, config.blur_kernel), 0)

        hsv = cv2.cvtColor(working, cv2.COLOR_BGR2HSV)

        lower_a = np.array(
            [config.hue_low_min, config.saturation_min, config.value_min], dtype=np.uint8
        )
        upper_a = np.array([config.hue_low_max, 255, 255], dtype=np.uint8)

        lower_b = np.array(
            [config.hue_high_min, config.saturation_min, config.value_min], dtype=np.uint8
        )
        upper_b = np.array([config.hue_high_max, 255, 255], dtype=np.uint8)

        mask = cv2.bitwise_or(
            cv2.inRange(hsv, lower_a, upper_a),
            cv2.inRange(hsv, lower_b, upper_b),
        )

        # The value channel is the intensity weight for the centroid.  A closing
        # step first fills the hole left by an over-exposed (white) laser core,
        # which would otherwise fall outside the red hue bands.
        response = hsv[:, :, 2]

        return mask, response

    def _difference_mask(
        self, frame_on: np.ndarray, frame_off: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(mask, response)`` for the laser-on minus laser-off method."""

        config = self.config

        on = frame_on
        off = frame_off

        if config.blur_kernel >= 3:
            kernel = (config.blur_kernel, config.blur_kernel)
            on = cv2.GaussianBlur(on, kernel, 0)
            off = cv2.GaussianBlur(off, kernel, 0)

        # Only brightening counts: the laser can only add light.
        difference = cv2.subtract(on, off)
        response = difference.max(axis=2) if difference.ndim == 3 else difference

        mask = (response >= config.difference_threshold).astype(np.uint8) * 255

        return mask, response.astype(np.uint8)

    def _clean_mask(self, mask: np.ndarray) -> np.ndarray:
        """Morphological clean-up plus removal of small connected components."""

        config = self.config

        if config.morphology_kernel >= 3:
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (config.morphology_kernel, config.morphology_kernel)
            )
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        if config.min_component_area > 0:
            count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

            if count > 1:
                areas = stats[1:, cv2.CC_STAT_AREA]
                keep = np.flatnonzero(areas >= config.min_component_area) + 1

                if len(keep) == 0:
                    return np.zeros_like(mask)

                mask = np.isin(labels, keep).astype(np.uint8) * 255

        return mask

    # ------------------------------------------------------------------
    # Sub-pixel extraction
    # ------------------------------------------------------------------
    def _extract_centroids(
        self, mask: np.ndarray, response: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Sub-pixel centroid per scan line.

        Works on rows; the caller transposes the inputs when the laser line is
        horizontal instead of vertical.

        Returns:
            ``(line_indices, centroids, thickness, weight_sum)`` for the scan
            lines that carry a usable laser response.  ``weight_sum`` is the
            integrated laser intensity of each line, which the caller turns
            into a confidence value.
        """

        config = self.config

        weights = np.where(mask > 0, response.astype(np.float64), 0.0)

        if not np.any(weights > 0):
            empty = np.zeros(0, dtype=np.float64)
            return empty, empty, empty, empty

        peak_columns = np.argmax(weights, axis=1)
        columns_index = np.arange(weights.shape[1])[None, :]

        # Restrict the centroid to a window around the strongest response so a
        # second stripe (for example the laser hitting the platform) cannot drag
        # the result towards the middle of the row.
        half_window = int(config.max_line_thickness)
        window = np.abs(columns_index - peak_columns[:, None]) <= half_window

        windowed = np.where(window, weights, 0.0)

        weight_sum = windowed.sum(axis=1)
        valid = weight_sum > 0

        centroids = np.full(len(weight_sum), np.nan, dtype=np.float64)
        np.divide(
            (windowed * columns_index).sum(axis=1),
            weight_sum,
            out=centroids,
            where=valid,
        )

        thickness = (windowed > 0).sum(axis=1).astype(np.float64)

        valid &= thickness >= config.min_line_thickness
        valid &= thickness <= config.max_line_thickness
        valid &= np.isfinite(centroids)

        lines = np.flatnonzero(valid).astype(np.float64)

        return lines, centroids[valid], thickness[valid], weight_sum[valid]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def detect(
        self,
        frame_bgr: np.ndarray,
        frame_without_laser: np.ndarray | None = None,
        *,
        keep_debug_images: bool = False,
    ) -> LaserDetection:
        """Detect the laser stripe in ``frame_bgr``.

        Args:
            frame_bgr: Laser-on frame, BGR uint8.
            frame_without_laser: Optional laser-off frame.  When supplied and
                the configured method is ``difference`` it is used for
                background subtraction.
            keep_debug_images: Keep the mask and response images on the result
                so the diagnostics tool can write them to disk.
        """

        if frame_bgr is None or frame_bgr.size == 0:
            return _empty_detection("Empty frame.")

        if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
            return _empty_detection("Frame must be a 3 channel BGR image.")

        config = self.config
        height, width = frame_bgr.shape[:2]

        roi_x, roi_y, roi_w, roi_h = self.roi_rect(height, width)
        roi_frame = frame_bgr[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w]

        use_difference = config.method == "difference" and frame_without_laser is not None

        if config.method == "difference" and frame_without_laser is None:
            return _empty_detection(
                "Difference method requires a laser-off frame; none was supplied."
            )

        if use_difference:
            if frame_without_laser.shape != frame_bgr.shape:
                return _empty_detection("Laser-on and laser-off frames have different sizes.")

            roi_off = frame_without_laser[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w]
            mask, response = self._difference_mask(roi_frame, roi_off)
        else:
            mask, response = self._hsv_mask(roi_frame)

        mask = self._clean_mask(mask)

        mask_pixels = int(np.count_nonzero(mask))

        statistics: dict[str, Any] = {
            "method": "difference" if use_difference else "hsv",
            "mask_pixels": mask_pixels,
            "roi": [roi_x, roi_y, roi_w, roi_h],
        }

        if mask_pixels == 0:
            return _empty_detection("No laser pixels survived thresholding.", statistics)

        transposed = config.scan_axis == "columns"

        work_mask = mask.T if transposed else mask
        work_response = response.T if transposed else response

        lines, centroids, thickness, weight_sum = self._extract_centroids(
            work_mask, work_response
        )

        if len(lines) == 0:
            return _empty_detection("No scan line produced a valid laser centroid.", statistics)

        # Confidence is relative to the strongest scan line in this frame: it
        # says "how well lit is this point compared with the best point here",
        # which is what matters when discarding weak tails of the stripe.
        reference = float(np.percentile(weight_sum, 95))

        if reference <= 0:
            return _empty_detection("Laser response is empty.", statistics)

        confidence = np.clip(weight_sum / reference, 0.0, 1.0)

        if transposed:
            # ``lines`` indexes columns and ``centroids`` indexes rows.
            u = lines + roi_x
            v = centroids + roi_y
        else:
            u = centroids + roi_x
            v = lines + roi_y

        pixels = np.column_stack([u, v]).astype(np.float64)

        keep = confidence >= config.min_confidence

        pixels = pixels[keep]
        confidence = confidence[keep]
        thickness = thickness[keep]

        statistics.update(
            {
                "raw_line_count": int(len(lines)),
                "kept_line_count": int(len(pixels)),
                "mean_thickness_px": float(np.mean(thickness)) if len(thickness) else 0.0,
                "mean_confidence": float(np.mean(confidence)) if len(confidence) else 0.0,
            }
        )

        accepted = len(pixels) >= config.min_points

        reason = "ok"

        if not accepted:
            reason = (
                f"Only {len(pixels)} laser points passed the confidence filter, "
                f"{config.min_points} required."
            )

        detection = LaserDetection(
            pixels=pixels,
            confidence=confidence,
            thickness=thickness,
            accepted=accepted,
            reason=reason,
            statistics=statistics,
        )

        if keep_debug_images:
            full_mask = np.zeros((height, width), dtype=np.uint8)
            full_mask[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w] = mask

            full_response = np.zeros((height, width), dtype=np.uint8)
            full_response[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w] = response

            detection.mask = full_mask
            detection.response = full_response

        return detection

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def build_overlay(self, frame_bgr: np.ndarray, detection: LaserDetection) -> np.ndarray:
        """Draw the detected centreline and ROI on top of the original frame."""

        overlay = frame_bgr.copy()

        height, width = overlay.shape[:2]
        roi_x, roi_y, roi_w, roi_h = self.roi_rect(height, width)

        cv2.rectangle(
            overlay,
            (roi_x, roi_y),
            (roi_x + roi_w - 1, roi_y + roi_h - 1),
            (80, 200, 255),
            1,
        )

        for (u, v), conf in zip(detection.pixels, detection.confidence):
            # Green where the laser is strong, fading to blue where it is weak.
            colour = (int(255 * (1.0 - conf)), int(255 * conf), 40)
            cv2.circle(overlay, (int(round(u)), int(round(v))), 1, colour, -1)

        return overlay


def detection_to_diagnostics(
    detector: LaserLineDetector,
    frame_bgr: np.ndarray,
    detection: LaserDetection,
) -> dict[str, np.ndarray]:
    """Bundle the images a human needs when aligning the physical laser."""

    images: dict[str, np.ndarray] = {"original": frame_bgr}

    if detection.mask is not None:
        images["mask"] = detection.mask

    if detection.response is not None:
        images["response"] = detection.response

    images["overlay"] = detector.build_overlay(frame_bgr, detection)

    return images
