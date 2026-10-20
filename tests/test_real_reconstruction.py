"""Deterministic checks of *real-input* reconstruction code, no Pi required.

Synthetic images are only test fixtures. The production endpoint never
generates such images or substitutes them for physical acquisitions.
"""
import json

import cv2
import numpy as np

from scanner_core import real_reconstruction as rec


def test_ray_plane_intersection_and_rotation():
    k = np.array([[800., 0., 320.], [0., 800., 240.], [0., 0., 1.]])
    image = np.array([[360., 240.], [360., 260.]], dtype=float)
    points = rec.project_laser_points(image, k, np.zeros(5), np.array([1., 0., 0.]), -25.)
    assert np.allclose(points[:, 0], 25.)
    assert np.allclose(points[:, 2], 500.)
    direction = np.array([0., 1., 0.])
    output = rec.rotate_back_and_filter(points, 90., np.array([0., 0., 500.]), direction)
    assert len(output) == 2
    assert np.allclose(np.linalg.norm(output[:, [0, 2]], axis=1), 25., atol=1e-5)


def test_extract_red_laser_center_from_photo():
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    img[100:380, 359:362, 2] = 250
    p = rec.stripe_pixels(img)
    assert len(p) > 80
    assert np.max(np.abs(p[:, 0] - 360.0)) < 0.5


def test_reject_missing_physical_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "SCANS_DIR", tmp_path / "scans")
    (tmp_path / "scans").mkdir()
    try:
        rec.find_recent_real_session()
        assert False, "Expected no real scan error"
    except rec.ReconstructionError as exc:
        assert "PHYSICAL" in str(exc)


def test_real_pipeline_consumes_photos_and_writes_ply(tmp_path, monkeypatch):
    calib = tmp_path / "calibration"
    scans = tmp_path / "scans"
    clouds = tmp_path / "point_clouds"
    calib.mkdir()
    folder = scans / "scan_20261011_130000"
    (folder / "captures").mkdir(parents=True)
    monkeypatch.setattr(rec, "CALIB_DIR", calib)
    monkeypatch.setattr(rec, "SCANS_DIR", scans)
    monkeypatch.setattr(rec, "POINT_DIR", clouds)

    camera = {
        "calibrated": True, "square_size_mm": 10., "image_size": [640, 480],
        "camera_matrix": [[800., 0., 320.], [0., 800., 240.], [0., 0., 1.]],
        "distortion_coefficients": [0., 0., 0., 0., 0.],
    }
    axis = {
        "calibrated": True, "axis_origin_camera_mm": [0., 0., 500.],
        "axis_direction_camera": [0., 1., 0.],
    }
    laser = {
        "calibrated": True, "square_size_mm": 10.,
        "plane_normal_camera": [1., 0., 0.], "plane_offset_mm": -25.,
    }
    for name, data in [
        ("camera_calibration.json", camera),
        ("turntable_calibration.json", axis),
        ("laser_calibration.json", laser),
    ]:
        (calib / name).write_text(json.dumps(data))
    captures = []
    for i in range(10):
        name = f"frame_{i:03}.jpg"
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        img[145:325, 359:362, 2] = 255
        assert cv2.imwrite(str(folder / "captures" / name), img)
        captures.append({
            "physical": True, "filename": name, "half_steps": i * 409,
            "angle_deg": i * 36.,
        })
    (folder / "metadata.json").write_text(json.dumps({
        "status": "finished", "capture_positions": captures,
    }))
    result = rec.reconstruct_latest()
    assert result["points"] > 120
    assert result["usable_frames"] == 10
    assert (clouds / result["file"]).is_file()
    header = (clouds / result["file"]).read_text().split("end_header")[0]
    assert "comment REAL scanner capture" in header
    assert f"element vertex {result['points']}" in header


def test_cyan_illumination_does_not_hide_the_red_laser():
    """Real laser pixels can have very high blue/green channels."""
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    img[:, :, 0] = 200
    img[:, :, 1] = 200
    img[100:380, 359:362, 2] = 65
    pixels = rec.stripe_pixels(img)
    assert len(pixels) >= 250
    assert np.max(np.abs(pixels[:, 0] - 360.0)) < 0.5


def test_flat_support_detection_removes_a_platform_trace():
    y = np.arange(100., 180.)
    pixels = np.column_stack((np.full(len(y), 320.), y))
    axis_origin = np.zeros(3)
    axis_direction = np.array([0., 1., 0.])
    h = np.linspace(0., 45., len(y))
    h[-18:] = 46.8
    radius = np.linspace(15., 25., len(y))
    radius[-18:] = np.linspace(26., 42., 18)
    points = np.column_stack((radius, h, np.zeros(len(h))))
    cleaned = rec.exclude_flat_support(
        pixels, points, axis_origin, axis_direction
    )
    assert 45 < len(cleaned) < len(points) - 10
    assert cleaned[:, 1].max() < 46.0


def test_support_detection_keeps_sloping_object():
    y = np.arange(100., 180.)
    pixels = np.column_stack((np.full(len(y), 320.), y))
    points = np.column_stack((
        np.full(len(y), 20.), np.linspace(-30., 30., len(y)),
        np.zeros(len(y))
    ))
    cleaned = rec.exclude_flat_support(
        pixels, points, np.zeros(3), np.array([0., 1., 0.])
    )
    assert len(cleaned) == len(points)
