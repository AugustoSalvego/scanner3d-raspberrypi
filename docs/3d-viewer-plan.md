# 3D Viewer Plan

This document describes the future plan for adding a 3D viewer to the Scanner3D Raspberry Pi project.

The 3D viewer is not implemented yet. This document defines how the feature should work in future versions of the software.

---

## Purpose

The 3D viewer will allow users to preview generated scan results directly in the browser.

Current workflow:

```text
Generate PLY
↓
Download PLY
↓
Open in CloudCompare or another external viewer
```

Future workflow:

```text
Generate PLY
↓
Preview in browser
↓
Download if needed
```

This will make the scanner interface more complete and easier to present in academic and portfolio contexts.

---

## Current Status

Current software already supports:

```text
- PLY generation
- PLY file download
- Point cloud file listing
- Scan sessions
- Metadata per scan
```

Not implemented yet:

```text
- Browser-based 3D viewer
- PLY rendering in the dashboard
- Mesh visualization
- Point cloud color support
- Camera navigation controls
```

---

## Target Features

The future 3D viewer should support:

```text
1. Load generated PLY files
2. Display point cloud in the browser
3. Rotate view
4. Zoom in/out
5. Pan camera
6. Reset view
7. Show basic file information
8. Later support mesh visualization
```

Possible future controls:

```text
- Rotate
- Zoom
- Pan
- Reset View
- Toggle Axes
- Toggle Grid
- Point Size
- Background Color
```

---

## Possible Technology

The most likely frontend technology is:

```text
Three.js
```

Why Three.js?

```text
- Runs in the browser
- Supports 3D visualization
- Can render point clouds
- Can load geometry files
- Good documentation
- No need for desktop software
```

Possible alternatives:

```text
- Plotly 3D
- Babylon.js
- Potree
- Custom WebGL renderer
```

For this project, Three.js is probably the best first option.

---

## Expected Viewer Section

A future dashboard section could look like this:

```text
3D Preview

Selected file:
point_cloud_scan_20260520_154207.ply

[Load Preview]
[Reset View]
[Download PLY]

Viewer:
interactive 3D canvas
```

Future visual layout:

```text
Generated Point Clouds
↓
Select file
↓
Preview in 3D Viewer
```

---

## Data Flow

Future flow:

```text
User selects PLY file
↓
Frontend requests file
↓
Three.js loads PLY data
↓
Point cloud appears in browser
↓
User rotates/zooms/pans
```

Possible route:

```text
GET /preview-ply/<filename>
```

or reuse:

```text
GET /download-ply/<filename>
```

The viewer can load the same file used for download.

---

## Point Cloud vs Mesh

The scanner will first generate point clouds.

A point cloud is a set of 3D points:

```text
x, y, z
```

A mesh is a surface made from connected triangles:

```text
vertices + faces
```

Current project stage:

```text
point cloud generation
```

Future stage:

```text
mesh generation
```

The viewer should first support point clouds. Mesh support can be added later.

---

## PLY File Types

PLY files may contain:

```text
vertices only
vertices + colors
vertices + faces
```

Current generated PLY files contain only:

```text
x
y
z
```

Future real scans may include:

```text
x
y
z
red
green
blue
```

Possible future PLY header:

```text
property float x
property float y
property float z
property uchar red
property uchar green
property uchar blue
```

---

## Future Viewer Backend Support

The backend may provide routes such as:

```text
GET /viewer/status
GET /viewer/files
GET /viewer/file/<filename>
```

Possible status response:

```json
{
  "viewer_available": false,
  "supported_formats": ["ply"],
  "mesh_support": false,
  "point_cloud_support": true
}
```

---

## Future Integration with Scan Sessions

Eventually, each scan session should allow previewing its generated point cloud.

Example:

```text
Scan Sessions

scan_20260520_154207
Captures: 10
Point Clouds: 1

[Preview]
[Download]
[Open Metadata]
```

This would make the dashboard much more useful for comparing scans.

---

## Not Implemented Yet

The following will not be implemented in the current version:

```text
Three.js viewer
PLY loader
mesh rendering
point cloud color support
3D camera controls
axes/grid display
session-based preview
```

These features will be developed after the physical scanner generates more useful real point clouds.

---

## Why Not Implement It Now?

The current PLY generation is still simulated.

It is better to first:

```text
1. finish software structure
2. integrate Raspberry Pi
3. capture real scan images
4. generate real point clouds
5. then implement browser preview
```

A viewer is more useful when the point cloud contains meaningful real data.

---

## Roadmap

Suggested roadmap:

```text
Phase 1:
Document viewer requirements

Phase 2:
Add viewer status endpoint

Phase 3:
Add placeholder viewer card in dashboard

Phase 4:
Add Three.js dependency

Phase 5:
Load simple PLY point cloud

Phase 6:
Add point size and camera controls

Phase 7:
Support session-based previews

Phase 8:
Support mesh visualization
```

---

## Summary

The 3D viewer is a planned feature that will make the Scanner3D system easier to use and present.

The first implementation should focus on simple point cloud preview.

Mesh visualization should only be added later, after real point cloud reconstruction becomes more stable.