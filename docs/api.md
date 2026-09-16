# API Documentation

This document describes the current HTTP API used by the Scanner3D web interface.

The API is built with Flask and is used by the browser dashboard to control the scanner software, monitor status, manage generated files, and interact with scan sessions.

---

## Base URL

When running locally:

```text
http://127.0.0.1:5000
```

When running on Raspberry Pi in the local network, the base URL may be:

```text
http://scanner.local:5000
```

or:

```text
http://<RASPBERRY_PI_IP>:5000
```

---

## Response Pattern

Most API routes follow this response format:

```json
{
  "success": true,
  "message": "Operation completed successfully.",
  "data": {}
}
```

Error responses usually follow this format:

```json
{
  "success": false,
  "message": "Error message.",
  "data": {}
}
```

Some routes may also include compatibility fields outside `data`, such as:

```json
{
  "success": true,
  "message": "Logs loaded successfully",
  "data": {
    "logs": []
  },
  "logs": []
}
```

This is done to keep the frontend compatible while the API is being improved.

---

# General Routes

## `GET /`

Loads the main web dashboard.

### Description

Returns the HTML interface used to control and monitor the scanner.

### Example

```text
GET /
```

### Expected Result

The browser loads the Scanner 3D Control Panel.

---

## `GET /health`

Checks if the backend service is online.

### Example

```text
GET /health
```

### Example Response

```json
{
  "success": true,
  "message": "Scanner service is online",
  "data": {
    "status": "ok",
    "scanner": "online"
  },
  "status": "ok",
  "scanner": "online"
}
```

### Purpose

Used to quickly verify if the Flask server is running.

---

## `GET /api-info`

Returns general API information.

### Example

```text
GET /api-info
```

### Example Response

```json
{
  "success": true,
  "message": "API information loaded successfully",
  "data": {
    "name": "Scanner 3D API",
    "version": "0.1",
    "simulation_mode": true,
    "routes": [
      "/",
      "/video",
      "/capture",
      "/clear-captures",
      "/reset-camera",
      "/start-scan",
      "/stop-scan",
      "/generate-ply",
      "/download-ply",
      "/point-clouds",
      "/download-ply/<filename>",
      "/delete-ply/<filename>",
      "/settings",
      "/simulation-mode",
      "/logs",
      "/status",
      "/health",
      "/api-info",
      "/scan-sessions"
    ]
  }
}
```

### Purpose

Useful for debugging, documentation and checking available API routes.

---

# Camera Routes

## `GET /video`

Streams the live camera feed.

### Example

```text
GET /video
```

### Description

Returns a multipart MJPEG video stream generated from OpenCV frames.

### Used By

The live camera image in the web dashboard.

### Notes

Camera controls such as brightness, contrast and exposure are intentionally not managed by the current interface because webcam control was unstable during notebook/Windows testing.

---

## `POST /capture`

Captures a single image from the current camera frame.

### Example

```text
POST /capture
```

### Example Success Response

```json
{
  "success": true,
  "message": "Image captured successfully",
  "data": {
    "file": "capture_manual_20260520_161230_420.jpg"
  },
  "file": "capture_manual_20260520_161230_420.jpg"
}
```

### Example Error Response

```json
{
  "success": false,
  "message": "Image capture failed",
  "data": {}
}
```

### Behavior

If there is no active scan session, the image is saved in:

```text
outputs/captures/
```

If there is an active scan session, the image is saved in:

```text
outputs/scans/<session_id>/captures/
```

---

## `GET /captures/<filename>`

Returns a captured image file.

### Example

```text
GET /captures/capture_manual_20260520_161230_420.jpg
```

### Used By

The frontend image thumbnails.

### Notes

This route is mainly used for manual test captures stored in `outputs/captures/`.

---

## `POST /clear-captures`

Deletes manual captured images from the default captures folder.

### Example

```text
POST /clear-captures
```

### Example Response

```json
{
  "success": true,
  "message": "Captures cleared successfully",
  "data": {
    "deleted": 3
  },
  "deleted": 3
}
```

### Behavior

Deletes `.jpg` files from:

```text
outputs/captures/
```

It does not delete captures inside scan sessions.

---

## `POST /reset-camera`

Releases the current camera object.

### Example

```text
POST /reset-camera
```

### Example Response

```json
{
  "success": true,
  "message": "Camera reset requested",
  "data": {}
}
```

### Purpose

Used to force the backend to release the OpenCV camera capture object. The camera is reinitialized when a new frame is requested.

---

# Scanner Control Routes

## `POST /start-scan`

Starts the scan pipeline.

### Example

```text
POST /start-scan
```

### Example Success Response

```json
{
  "success": true,
  "message": "Scan started successfully",
  "data": {}
}
```

### Example Error Response

```json
{
  "success": false,
  "message": "Scan is already running",
  "data": {}
}
```

### Behavior

Starts the scan pipeline in a background thread.

Current simulated flow:

```text
Create scan session
↓
Rotate motor step
↓
Wait step delay
↓
Capture image
↓
Wait capture delay
↓
Repeat
↓
Generate point cloud
↓
Finish scan session
```

### Future Behavior

When integrated with Raspberry Pi hardware, this route will trigger the real scan process:

```text
real motor movement
real camera capture
laser line extraction
point cloud reconstruction
```

---

## `POST /stop-scan`

Requests scan interruption.

### Example

```text
POST /stop-scan
```

### Example Response

```json
{
  "success": true,
  "message": "Stop scan requested",
  "data": {}
}
```

### Behavior

Changes scanner state so the running pipeline can stop safely.

---

# Point Cloud Routes

## `POST /generate-ply`

Generates a `.ply` point cloud file.

### Example

```text
POST /generate-ply
```

### Example Success Response

```json
{
  "success": true,
  "message": "PLY generated successfully",
  "data": {
    "file": "outputs/point_clouds/point_cloud_manual_20260520_161400_123.ply"
  },
  "file": "outputs/point_clouds/point_cloud_manual_20260520_161400_123.ply"
}
```

### Example Error Response

```json
{
  "success": false,
  "message": "PLY generation failed",
  "data": {
    "error": "Error description"
  }
}
```

### Current Behavior

Currently generates a simplified/simulated point cloud.

### Future Behavior

This route will be improved to generate real point clouds based on:

```text
captured scan images
laser line extraction
camera geometry
turntable angle
triangulation
```

---

## `GET /download-ply`

Downloads the last generated PLY file.

### Example

```text
GET /download-ply
```

### Behavior

The route first tries to download the latest file stored in:

```text
scanner_state["last_point_cloud"]
```

If that does not exist, it falls back to the default point cloud file path.

### Example Error Response

```json
{
  "success": false,
  "message": "PLY file not found. Generate PLY first.",
  "data": {
    "expected_path": "outputs/point_clouds/scan_result.ply"
  }
}
```

---

## `GET /point-clouds`

Lists generated point cloud files.

### Example

```text
GET /point-clouds
```

### Example Response

```json
{
  "success": true,
  "message": "Point cloud files loaded successfully",
  "data": {
    "files": [
      {
        "name": "point_cloud_manual_20260520_161400_123.ply",
        "size_kb": 5.53,
        "modified_at": "2026-05-20 16:14:00"
      }
    ]
  },
  "files": [
    {
      "name": "point_cloud_manual_20260520_161400_123.ply",
      "size_kb": 5.53,
      "modified_at": "2026-05-20 16:14:00"
    }
  ]
}
```

### Used By

The "Generated Point Clouds" table in the dashboard.

---

## `GET /download-ply/<filename>`

Downloads a specific PLY file from the generated point cloud list.

### Example

```text
GET /download-ply/point_cloud_manual_20260520_161400_123.ply
```

### Behavior

Uses a safe filename validation step before sending the file.

### Example Error Response

```json
{
  "success": false,
  "message": "PLY file not found",
  "data": {
    "file": "point_cloud_manual_20260520_161400_123.ply"
  }
}
```

---

## `POST /delete-ply/<filename>`

Deletes a specific PLY file from the generated point cloud folder.

### Example

```text
POST /delete-ply/point_cloud_manual_20260520_161400_123.ply
```

### Example Success Response

```json
{
  "success": true,
  "message": "PLY deleted successfully",
  "data": {
    "file": "point_cloud_manual_20260520_161400_123.ply"
  }
}
```

### Example Error Response

```json
{
  "success": false,
  "message": "PLY file not found",
  "data": {
    "file": "point_cloud_manual_20260520_161400_123.ply"
  }
}
```

---

# Settings Routes

## `GET /settings`

Returns current runtime scanner settings.

### Example

```text
GET /settings
```

### Example Response

```json
{
  "success": true,
  "message": "Settings loaded successfully",
  "data": {
    "settings": {
      "scan_steps": 10,
      "step_delay": 1.0,
      "capture_delay": 0.5,
      "simulation_mode": true
    }
  },
  "settings": {
    "scan_steps": 10,
    "step_delay": 1.0,
    "capture_delay": 0.5,
    "simulation_mode": true
  }
}
```

---

## `POST /settings`

Updates runtime scanner settings.

### Example Request

```json
{
  "scan_steps": 20,
  "step_delay": 0.5,
  "capture_delay": 0.3
}
```

### Example Success Response

```json
{
  "success": true,
  "message": "Settings updated successfully",
  "data": {
    "settings": {
      "scan_steps": 20,
      "step_delay": 0.5,
      "capture_delay": 0.3,
      "simulation_mode": true
    }
  }
}
```

### Example Error Response

```json
{
  "success": false,
  "message": "Settings update failed",
  "data": {
    "errors": {
      "scan_steps": "Scan steps must be greater than zero."
    }
  }
}
```

---

## `GET /simulation-mode`

Returns current simulation mode state.

### Example

```text
GET /simulation-mode
```

### Example Response

```json
{
  "success": true,
  "message": "Simulation mode loaded successfully",
  "data": {
    "simulation_mode": true
  },
  "simulation_mode": true
}
```

---

## `POST /simulation-mode`

Toggles or sets simulation mode.

### Toggle Example

```text
POST /simulation-mode
```

### Force ON Example

```json
{
  "enabled": true
}
```

### Force OFF Example

```json
{
  "enabled": false
}
```

### Example Response

```json
{
  "success": true,
  "message": "Simulation Mode: ON",
  "data": {
    "simulation_mode": true
  },
  "simulation_mode": true
}
```

### Purpose

Simulation mode allows development without physical scanner hardware.

When enabled:

```text
motor is simulated
point cloud is simulated
hardware GPIO is not used
```

When disabled in the future:

```text
real Raspberry Pi GPIO motor control
real scanning pipeline
real hardware integration
```

---

# Status and Logs Routes

## `GET /status`

Returns the current scanner state.

### Example

```text
GET /status
```

### Example Response

```json
{
  "running": false,
  "status": "IDLE",
  "simulation_mode": true,
  "motor_position": 0,
  "frames_captured": 0,
  "last_capture": null,
  "point_cloud_generated": false,
  "last_point_cloud": null,
  "current_session": null,
  "last_session": null,
  "last_scan_time": null,
  "last_error": null,
  "scanner_version": "0.1"
}
```

### Used By

The Scanner Status card in the web dashboard.

---

## `GET /logs`

Returns the current system logs.

### Example

```text
GET /logs
```

### Example Response

```json
{
  "success": true,
  "message": "Logs loaded successfully",
  "data": {
    "logs": [
      "[16:10:20] Scan started",
      "[16:10:21] Motor step -> 1"
    ]
  },
  "logs": [
    "[16:10:20] Scan started",
    "[16:10:21] Motor step -> 1"
  ]
}
```

---

# Scan Session Routes

## `GET /scan-sessions`

Lists scan sessions generated by the pipeline.

### Example

```text
GET /scan-sessions
```

### Example Response

```json
{
  "success": true,
  "message": "Scan sessions loaded successfully",
  "data": {
    "sessions": [
      {
        "session_id": "scan_20260520_154207",
        "created_at": "2026-05-20 15:42:07",
        "finished_at": "2026-05-20 15:42:11",
        "status": "finished",
        "captures": [
          "capture_step_001_20260520_154208_000.jpg"
        ],
        "point_clouds": [
          "point_cloud_scan_20260520_154207_20260520_154212_000.ply"
        ]
      }
    ]
  }
}
```

### Behavior

Reads metadata from:

```text
outputs/scans/<session_id>/metadata.json
```

### Purpose

Used to track scan experiments and organize scan results.

---

# Development Notes

## Current Software Mode

The current web interface is suitable for:

```text
software development
interface testing
scan pipeline simulation
file management
session management
basic API validation
```

## Not Yet Implemented

The following features are planned but not yet fully implemented:

```text
real GPIO motor control
real laser triangulation
camera calibration
real point cloud reconstruction
mesh generation
3D browser preview
```

---

# Testing Checklist

Use this checklist after major API changes:

```text
GET  /health
GET  /api-info
GET  /settings
GET  /simulation-mode
GET  /status
GET  /logs
GET  /point-clouds
GET  /scan-sessions

POST /capture
POST /clear-captures
POST /start-scan
POST /stop-scan
POST /generate-ply
GET  /download-ply
GET  /download-ply/<filename>
POST /delete-ply/<filename>
POST /settings
POST /simulation-mode
```