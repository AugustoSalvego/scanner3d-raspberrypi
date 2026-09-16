"""Point cloud container, PLY input/output and filtering.

The PLY writer emits a correct header with the real vertex count and supports
optional colour and confidence properties.  Binary little-endian is the default
because a real scan easily reaches hundreds of thousands of points and ASCII
would be several times larger and much slower to parse on a Raspberry Pi.

Filtering is deliberately NumPy-only for everything that runs on every scan:
voxel down-sampling and the grid based radius filter are ``O(n log n)`` and need
no extra dependency.  Statistical outlier removal needs true k-nearest-neighbour
distances, so it is used only when SciPy happens to be installed and is reported
as skipped otherwise - it is never silently approximated.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from scanner_core.config import FilterConfig
from scanner_core.errors import ReconstructionError

PLY_SCHEMA_VERSION = 1

_ASCII_TYPES = {
    "float": np.float32,
    "float32": np.float32,
    "double": np.float64,
    "float64": np.float64,
    "uchar": np.uint8,
    "uint8": np.uint8,
    "char": np.int8,
    "int8": np.int8,
    "ushort": np.uint16,
    "uint16": np.uint16,
    "short": np.int16,
    "int16": np.int16,
    "uint": np.uint32,
    "uint32": np.uint32,
    "int": np.int32,
    "int32": np.int32,
}


@dataclass
class PointCloud:
    """A set of 3D points with optional per-point colour and confidence."""

    points: np.ndarray
    colors: np.ndarray | None = None
    confidence: np.ndarray | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.points = np.asarray(self.points, dtype=np.float64).reshape(-1, 3)

        if self.colors is not None:
            self.colors = np.asarray(self.colors, dtype=np.uint8).reshape(-1, 3)

            if len(self.colors) != len(self.points):
                raise ValueError("colors must have the same length as points.")

        if self.confidence is not None:
            self.confidence = np.asarray(self.confidence, dtype=np.float64).reshape(-1)

            if len(self.confidence) != len(self.points):
                raise ValueError("confidence must have the same length as points.")

    def __len__(self) -> int:
        return int(len(self.points))

    @property
    def is_empty(self) -> bool:
        return len(self.points) == 0

    def select(self, mask: np.ndarray) -> "PointCloud":
        """Return a new cloud keeping only the rows selected by ``mask``."""

        mask = np.asarray(mask)

        return PointCloud(
            points=self.points[mask],
            colors=None if self.colors is None else self.colors[mask],
            confidence=None if self.confidence is None else self.confidence[mask],
            metadata=dict(self.metadata),
        )

    def bounds(self) -> dict[str, list[float]] | None:
        if self.is_empty:
            return None

        return {
            "min": [float(value) for value in self.points.min(axis=0)],
            "max": [float(value) for value in self.points.max(axis=0)],
        }

    def summary(self) -> dict[str, Any]:
        bounds = self.bounds()

        summary: dict[str, Any] = {
            "point_count": len(self),
            "has_colors": self.colors is not None,
            "has_confidence": self.confidence is not None,
            "bounds_mm": bounds,
        }

        if bounds is not None:
            summary["size_mm"] = [
                round(bounds["max"][index] - bounds["min"][index], 3) for index in range(3)
            ]

        if self.confidence is not None and len(self.confidence):
            summary["mean_confidence"] = float(np.mean(self.confidence))

        return summary

    @classmethod
    def concatenate(cls, clouds: list["PointCloud"]) -> "PointCloud":
        clouds = [cloud for cloud in clouds if not cloud.is_empty]

        if not clouds:
            return cls(points=np.zeros((0, 3), dtype=np.float64))

        points = np.vstack([cloud.points for cloud in clouds])

        has_colors = all(cloud.colors is not None for cloud in clouds)
        has_confidence = all(cloud.confidence is not None for cloud in clouds)

        colors = np.vstack([cloud.colors for cloud in clouds]) if has_colors else None
        confidence = (
            np.concatenate([cloud.confidence for cloud in clouds]) if has_confidence else None
        )

        return cls(points=points, colors=colors, confidence=confidence)


# ----------------------------------------------------------------------
# PLY writing
# ----------------------------------------------------------------------
def write_ply(
    path: Path | str,
    cloud: PointCloud,
    *,
    binary: bool = True,
    comments: list[str] | None = None,
) -> Path:
    """Write ``cloud`` to ``path`` as a PLY file.

    Raises:
        ReconstructionError: If the cloud has no points.  An empty PLY is never
            written, because a zero-vertex file looks like a successful scan in
            a file listing while carrying no data at all.
    """

    if cloud.is_empty:
        raise ReconstructionError("Refusing to write an empty point cloud.")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    count = len(cloud)
    has_colors = cloud.colors is not None
    has_confidence = cloud.confidence is not None

    header_lines = [
        "ply",
        f"format {'binary_little_endian' if binary else 'ascii'} 1.0",
        f"comment Scanner3D point cloud, schema {PLY_SCHEMA_VERSION}",
    ]

    for comment in comments or []:
        for line in str(comment).splitlines():
            header_lines.append(f"comment {line}")

    header_lines.append(f"element vertex {count}")
    header_lines.extend(["property float x", "property float y", "property float z"])

    if has_colors:
        header_lines.extend(
            ["property uchar red", "property uchar green", "property uchar blue"]
        )

    if has_confidence:
        header_lines.append("property float confidence")

    header_lines.append("end_header")

    header = "\n".join(header_lines) + "\n"

    if binary:
        dtype_fields: list[tuple[str, str]] = [("x", "<f4"), ("y", "<f4"), ("z", "<f4")]

        if has_colors:
            dtype_fields.extend([("red", "u1"), ("green", "u1"), ("blue", "u1")])

        if has_confidence:
            dtype_fields.append(("confidence", "<f4"))

        records = np.empty(count, dtype=np.dtype(dtype_fields))
        records["x"] = cloud.points[:, 0]
        records["y"] = cloud.points[:, 1]
        records["z"] = cloud.points[:, 2]

        if has_colors:
            records["red"] = cloud.colors[:, 0]
            records["green"] = cloud.colors[:, 1]
            records["blue"] = cloud.colors[:, 2]

        if has_confidence:
            records["confidence"] = cloud.confidence

        with path.open("wb") as handle:
            handle.write(header.encode("ascii"))
            handle.write(records.tobytes())
    else:
        columns: list[np.ndarray] = [
            cloud.points[:, 0],
            cloud.points[:, 1],
            cloud.points[:, 2],
        ]
        formats = ["%.6f", "%.6f", "%.6f"]

        if has_colors:
            columns.extend([cloud.colors[:, 0], cloud.colors[:, 1], cloud.colors[:, 2]])
            formats.extend(["%d", "%d", "%d"])

        if has_confidence:
            columns.append(cloud.confidence)
            formats.append("%.4f")

        table = np.column_stack(columns)

        with path.open("w", encoding="ascii", newline="\n") as handle:
            handle.write(header)
            np.savetxt(handle, table, fmt=" ".join(formats))

    return path


# ----------------------------------------------------------------------
# PLY reading
# ----------------------------------------------------------------------
def _parse_ply_header(handle: BinaryIO) -> tuple[str, int, list[tuple[str, str]], list[str]]:
    magic = handle.readline().strip()

    if magic != b"ply":
        raise ReconstructionError("Not a PLY file: missing 'ply' magic line.")

    ply_format = ""
    vertex_count = 0
    properties: list[tuple[str, str]] = []
    comments: list[str] = []
    in_vertex_element = False

    while True:
        raw = handle.readline()

        if not raw:
            raise ReconstructionError("Malformed PLY: header ended without 'end_header'.")

        line = raw.decode("ascii", errors="replace").strip()

        if not line:
            continue

        parts = line.split()
        keyword = parts[0].lower()

        if keyword == "end_header":
            break

        if keyword == "format":
            ply_format = parts[1].lower()
        elif keyword == "comment":
            comments.append(" ".join(parts[1:]))
        elif keyword == "element":
            in_vertex_element = parts[1].lower() == "vertex"

            if in_vertex_element:
                vertex_count = int(parts[2])
        elif keyword == "property" and in_vertex_element:
            if parts[1].lower() == "list":
                raise ReconstructionError("List properties on vertices are not supported.")

            properties.append((parts[1].lower(), parts[2].lower()))

    if ply_format not in {"ascii", "binary_little_endian"}:
        raise ReconstructionError(f"Unsupported PLY format: {ply_format or 'unknown'}.")

    return ply_format, vertex_count, properties, comments


def read_ply(path: Path | str) -> PointCloud:
    """Read a PLY point cloud.

    Supports ASCII and binary little-endian files with scalar vertex
    properties, which covers everything this project writes plus the usual
    output of MeshLab and CloudCompare.
    """

    path = Path(path)

    if not path.exists():
        raise ReconstructionError(f"Point cloud file not found: {path.name}")

    with path.open("rb") as handle:
        ply_format, vertex_count, properties, comments = _parse_ply_header(handle)

        names = [name for _, name in properties]

        for axis in ("x", "y", "z"):
            if axis not in names:
                raise ReconstructionError(f"PLY file has no '{axis}' vertex property.")

        if vertex_count == 0:
            return PointCloud(points=np.zeros((0, 3), dtype=np.float64))

        if ply_format == "binary_little_endian":
            dtype = np.dtype(
                [
                    (name, _numpy_descr(type_name))
                    for type_name, name in properties
                ]
            )

            buffer = handle.read(dtype.itemsize * vertex_count)

            if len(buffer) < dtype.itemsize * vertex_count:
                raise ReconstructionError("PLY file is truncated.")

            records = np.frombuffer(buffer, dtype=dtype, count=vertex_count)
            columns = {name: np.asarray(records[name]) for name in names}
        else:
            text = handle.read().decode("ascii", errors="replace")
            rows = [line.split() for line in text.splitlines() if line.strip()]

            if len(rows) < vertex_count:
                raise ReconstructionError("PLY file is truncated.")

            table = np.array(rows[:vertex_count], dtype=np.float64)
            columns = {name: table[:, index] for index, name in enumerate(names)}

    points = np.column_stack([columns["x"], columns["y"], columns["z"]]).astype(np.float64)

    colors = None

    if {"red", "green", "blue"}.issubset(columns):
        colors = np.column_stack(
            [columns["red"], columns["green"], columns["blue"]]
        ).astype(np.uint8)

    confidence = None

    if "confidence" in columns:
        confidence = np.asarray(columns["confidence"], dtype=np.float64)

    return PointCloud(
        points=points,
        colors=colors,
        confidence=confidence,
        metadata={"comments": comments, "source": path.name},
    )


def _numpy_descr(type_name: str) -> str:
    numpy_type = _ASCII_TYPES.get(type_name)

    if numpy_type is None:
        raise ReconstructionError(f"Unsupported PLY property type: {type_name}")

    descriptor = np.dtype(numpy_type).str

    # Force little-endian for multi-byte types; PLY already told us the order.
    if descriptor[0] in "<>|":
        descriptor = ("<" if np.dtype(numpy_type).itemsize > 1 else "|") + descriptor[1:]

    return descriptor


# ----------------------------------------------------------------------
# Filtering
# ----------------------------------------------------------------------
def remove_non_finite(cloud: PointCloud) -> PointCloud:
    """Drop rows containing NaN or infinity."""

    if cloud.is_empty:
        return cloud

    mask = np.all(np.isfinite(cloud.points), axis=1)

    return cloud.select(mask)


def voxel_downsample(cloud: PointCloud, voxel_size_mm: float) -> PointCloud:
    """Average the points falling inside each cubic voxel.

    Pure NumPy, ``O(n log n)``: voxel indices are hashed into a single integer
    key when that is safe, then ``np.bincount`` accumulates the per-voxel sums.
    """

    if cloud.is_empty or voxel_size_mm <= 0:
        return cloud

    keys = np.floor(cloud.points / float(voxel_size_mm)).astype(np.int64)
    keys -= keys.min(axis=0)

    dimensions = keys.max(axis=0) + 1

    # Guard against overflow on a huge, sparse bounding box.
    if float(dimensions[0]) * float(dimensions[1]) * float(dimensions[2]) < 4e18:
        flat = (
            keys[:, 0] * (dimensions[1] * dimensions[2])
            + keys[:, 1] * dimensions[2]
            + keys[:, 2]
        )
        _, inverse, counts = np.unique(flat, return_inverse=True, return_counts=True)
    else:
        _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)

    inverse = inverse.reshape(-1)
    voxel_count = len(counts)

    averaged = np.column_stack(
        [
            np.bincount(inverse, weights=cloud.points[:, axis], minlength=voxel_count)
            for axis in range(3)
        ]
    ) / counts[:, None]

    colors = None

    if cloud.colors is not None:
        colors = np.column_stack(
            [
                np.bincount(
                    inverse, weights=cloud.colors[:, channel].astype(np.float64),
                    minlength=voxel_count,
                )
                for channel in range(3)
            ]
        ) / counts[:, None]
        colors = np.clip(np.round(colors), 0, 255).astype(np.uint8)

    confidence = None

    if cloud.confidence is not None:
        confidence = (
            np.bincount(inverse, weights=cloud.confidence, minlength=voxel_count) / counts
        )

    return PointCloud(
        points=averaged,
        colors=colors,
        confidence=confidence,
        metadata=dict(cloud.metadata),
    )


def radius_outlier_removal(
    cloud: PointCloud,
    radius_mm: float,
    min_neighbours: int,
) -> tuple[PointCloud, int, str]:
    """Drop points that sit alone in space.

    Implemented with a uniform spatial hash whose cell size equals ``radius_mm``:
    a point keeps its neighbours from the 3x3x3 block of cells around it.  That
    block encloses the sphere of radius ``radius_mm``, so the neighbour count is
    an *over*-estimate and the filter only ever removes points that are
    unambiguously isolated.  This conservative bias is deliberate - it is far
    better than deleting good surface points.

    Returns:
        ``(cloud, removed, note)``.  ``note`` explains why nothing was removed
        when the filter could not run, so the caller never reports a filter as
        applied when it was skipped.
    """

    if cloud.is_empty:
        return cloud, 0, "skipped: empty cloud"

    if radius_mm <= 0 or min_neighbours <= 0:
        return cloud, 0, "skipped: filter disabled by configuration"

    keys = np.floor(cloud.points / float(radius_mm)).astype(np.int64)
    keys -= keys.min(axis=0)

    dimensions = keys.max(axis=0) + 3  # +2 margin for the neighbour offsets

    if float(dimensions[0]) * float(dimensions[1]) * float(dimensions[2]) >= 4e18:
        return (
            cloud,
            0,
            "skipped: point spread is too large for the spatial grid; "
            "tighten the reconstruction depth/radius limits first",
        )

    stride_y = dimensions[2]
    stride_x = dimensions[1] * dimensions[2]

    flat = keys[:, 0] * stride_x + keys[:, 1] * stride_y + keys[:, 2]

    unique_keys, counts = np.unique(flat, return_counts=True)

    neighbours = np.zeros(len(cloud.points), dtype=np.int64)

    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                shifted = flat + dx * stride_x + dy * stride_y + dz

                positions = np.searchsorted(unique_keys, shifted)
                positions = np.clip(positions, 0, len(unique_keys) - 1)

                hit = unique_keys[positions] == shifted
                neighbours += np.where(hit, counts[positions], 0)

    # A point always counts itself.
    keep = (neighbours - 1) >= min_neighbours
    removed = int(np.count_nonzero(~keep))

    return cloud.select(keep), removed, "ok"


def statistical_outlier_removal(
    cloud: PointCloud,
    neighbours: int,
    std_ratio: float,
) -> tuple[PointCloud, int, str]:
    """Remove points whose mean distance to their ``k`` nearest neighbours is
    an outlier.

    Requires SciPy's KD-tree.  When SciPy is not installed the cloud is returned
    unchanged together with a reason, so the caller can report honestly that the
    filter did not run instead of pretending it did.
    """

    if cloud.is_empty:
        return cloud, 0, "empty cloud"

    try:
        from scipy.spatial import cKDTree
    except ImportError:
        return cloud, 0, "skipped: SciPy is not installed"

    k = int(neighbours) + 1

    if len(cloud.points) <= k:
        return cloud, 0, "skipped: not enough points"

    tree = cKDTree(cloud.points)
    distances, _ = tree.query(cloud.points, k=k, workers=-1)

    # Column 0 is the point itself, at distance 0.
    mean_distances = distances[:, 1:].mean(axis=1)

    threshold = float(mean_distances.mean() + std_ratio * mean_distances.std())

    keep = mean_distances <= threshold
    removed = int(np.count_nonzero(~keep))

    return cloud.select(keep), removed, "ok"


def filter_point_cloud(
    cloud: PointCloud,
    config: FilterConfig,
) -> tuple[PointCloud, dict[str, Any]]:
    """Apply the configured clean-up chain and report what each stage did."""

    started = time.perf_counter()

    statistics: dict[str, Any] = {"points_before_filter": len(cloud)}

    working = remove_non_finite(cloud)
    statistics["removed_non_finite"] = len(cloud) - len(working)

    if config.min_confidence > 0 and working.confidence is not None and not working.is_empty:
        before = len(working)
        working = working.select(working.confidence >= config.min_confidence)
        statistics["removed_low_confidence"] = before - len(working)
    else:
        statistics["removed_low_confidence"] = 0

    if config.voxel_size_mm > 0 and not working.is_empty:
        before = len(working)
        working = voxel_downsample(working, config.voxel_size_mm)
        statistics["removed_by_voxel_downsample"] = before - len(working)
        statistics["voxel_size_mm"] = config.voxel_size_mm
    else:
        statistics["removed_by_voxel_downsample"] = 0

    too_large = len(working) > config.max_points_for_outlier_filters

    if config.radius_outlier_enabled and not working.is_empty and not too_large:
        working, removed, note = radius_outlier_removal(
            working, config.radius_outlier_radius_mm, config.radius_outlier_min_neighbours
        )
        statistics["removed_by_radius_filter"] = removed
        statistics["radius_filter_note"] = note
    else:
        statistics["removed_by_radius_filter"] = 0

        if config.radius_outlier_enabled and too_large:
            statistics["radius_filter_note"] = "skipped: cloud above max_points_for_outlier_filters"

    if config.statistical_outlier_enabled and not working.is_empty and not too_large:
        working, removed, note = statistical_outlier_removal(
            working, config.statistical_neighbours, config.statistical_std_ratio
        )
        statistics["removed_by_statistical_filter"] = removed
        statistics["statistical_filter_note"] = note
    else:
        statistics["removed_by_statistical_filter"] = 0

        if config.statistical_outlier_enabled and too_large:
            statistics["statistical_filter_note"] = (
                "skipped: cloud above max_points_for_outlier_filters"
            )

    statistics["points_after_filter"] = len(working)
    statistics["filter_seconds"] = round(time.perf_counter() - started, 3)

    return working, statistics
