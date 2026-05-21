# Software Architecture

This document describes the current software architecture of the Scanner3D Raspberry Pi project.

The goal of this architecture is to keep the project organized, modular and ready for future hardware integration with Raspberry Pi, camera, laser and motor control.

---

## Overview

The software is organized into separate layers:

```text
Web Interface
↓
Flask API
↓
Scanner Core
↓
Camera / Motor / Point Cloud / Sessions
↓
Outputs
```

The main idea is to avoid mixing the web interface, scanner logic, hardware logic and generated files in the same place.

---

## Project Structure

```text
scanner3d-raspberrypi/
│
├── scanner_core/
│   ├── __init__.py
│   ├── camera.py
│   ├── config.py
│   ├── logger.py
│   ├── motor.py
│   ├── pipeline.py
│   ├── point_cloud.py
│   ├── runtime_settings.py
│   ├── session.py
│   ├── state.py
│   └── status.py
│
├── web_interface/
│   ├── __init__.py
│   ├── app.py
│   ├── api_response.py
│   └── templates/
│       └── index.html
│
├── tools/
│   ├── camera test scripts
│   ├── GPIO test scripts
│   └── debugging scripts
│
├── outputs/
│   ├── captures/
│   ├── point_clouds/
│   └── scans/
│
├── docs/
│   ├── api.md
│   └── software-architecture.md
│
├── README.md
├── requirements.txt
└── .gitignore
```

---

# Main Layers

## 1. Web Interface Layer

Location:

```text
web_interface/
```

This layer contains the Flask application and the HTML dashboard.

Main responsibilities:

```text
- Render the dashboard
- Provide API routes
- Receive button actions from the browser
- Return scanner status
- Manage files through HTTP routes
- Show logs, sessions, settings and generated files
```

Important files:

```text
web_interface/app.py
web_interface/api_response.py
web_interface/templates/index.html
```

---

## 2. Scanner Core Layer

Location:

```text
scanner_core/
```

This layer contains the scanner logic.

Main responsibilities:

```text
- Camera access
- Scan pipeline execution
- Motor abstraction
- Point cloud generation
- Runtime settings
- Session management
- Scanner state
- System logs
```

This layer should not depend heavily on the visual interface. The web interface calls the scanner core, but the scanner core should remain usable even if the interface changes.

---

## 3. Outputs Layer

Location:

```text
outputs/
```

This folder stores generated files such as:

```text
- manual captured images
- generated point clouds
- scan session folders
- metadata files
```

This folder is ignored by Git because generated scan data should not be stored in the repository.

---

## 4. Tools Layer

Location:

```text
tools/
```

This folder contains utility scripts used during development and debugging.

Examples:

```text
- camera tests
- GPIO tests
- stepper motor tests
- red laser detection tests
```

These scripts are useful during development but are not part of the main web application.

---

# Scanner Core Modules

## `camera.py`

Responsible for camera handling.

Current responsibilities:

```text
- Initialize OpenCV camera
- Read frames
- Encode frames for live video stream
- Capture images
- Save images to output folders
- Save captures inside scan sessions when a session is active
```

Current behavior:

```text
Manual capture:
outputs/captures/

Scan session capture:
outputs/scans/<session_id>/captures/
```

Future responsibilities:

```text
- Raspberry Pi camera support
- Better camera backend selection
- Camera resolution configuration
- Safe camera calibration support
```

Important design decision:

Camera brightness, contrast and exposure controls are intentionally disabled in the current interface because webcam control through OpenCV was unstable during Windows/notebook testing.

---

## `motor.py`

Responsible for motor control abstraction.

Current behavior:

```text
- Simulates motor steps
- Updates motor position
- Writes motor movement to logs
```

Current simulated flow:

```text
rotate_step()
↓
increase motor position
↓
add log entry
```

Future behavior:

```text
Simulation Mode ON:
- simulated motor movement

Simulation Mode OFF:
- real GPIO motor movement
```

Possible future hardware targets:

```text
- 28BYJ-48 with ULN2003 driver
- NEMA 17 with DRV8825 driver
```

---

## `pipeline.py`

Responsible for the scan execution flow.

Current responsibilities:

```text
- Start a scan
- Create a scan session
- Read runtime settings
- Rotate motor step by step
- Capture one frame per step
- Generate point cloud
- Finish scan session
- Update scanner state
- Handle interruptions and errors
```

Current simplified scan flow:

```text
Start Scan
↓
Check if scanner is already running
↓
Load runtime settings
↓
Create scan session
↓
For each scan step:
    rotate motor
    wait step delay
    capture image
    wait capture delay
↓
Generate point cloud
↓
Finish session
↓
Update status
```

Future real scan flow:

```text
Start Scan
↓
Move real motor
↓
Wait for platform stabilization
↓
Capture image with laser line
↓
Extract laser line
↓
Convert laser line to 3D points
↓
Repeat for all angles
↓
Generate real point cloud
↓
Optional mesh generation
```

---

## `point_cloud.py`

Responsible for point cloud generation.

Current behavior:

```text
- Generates a simplified/simulated PLY file
- Saves it to the default point cloud folder or current scan session
- Updates scanner state
- Registers generated point clouds in session metadata
```

Current output locations:

```text
Manual generation:
outputs/point_clouds/

Scan session generation:
outputs/scans/<session_id>/point_clouds/
```

Current point cloud generation is still simplified and does not yet perform real laser triangulation.

Future responsibilities:

```text
- Read captured scan images
- Detect red laser line
- Extract line center
- Filter noise
- Estimate 3D coordinates
- Generate real PLY point cloud
- Optionally prepare data for mesh generation
```

---

## `session.py`

Responsible for scan session organization.

Current responsibilities:

```text
- Create scan session folders
- Create captures folder
- Create point_clouds folder
- Create metadata.json
- Store capture filenames
- Store generated point cloud filenames
- Mark sessions as finished
- List previous scan sessions
```

Session folder structure:

```text
outputs/
└── scans/
    └── scan_YYYYMMDD_HHMMSS/
        ├── captures/
        ├── point_clouds/
        └── metadata.json
```

Example metadata:

```json
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
```

Why sessions are important:

```text
- Keep each scan organized
- Avoid mixing files from different experiments
- Help with TCC validation
- Make debugging easier
- Allow future comparison between scans
```

---

## `runtime_settings.py`

Responsible for settings that can be changed while the software is running.

Current settings:

```text
scan_steps
step_delay
capture_delay
simulation_mode
```

These values are used by the pipeline to control scan behavior.

Current examples:

```json
{
  "scan_steps": 10,
  "step_delay": 1.0,
  "capture_delay": 0.5,
  "simulation_mode": true
}
```

Future settings may include:

```text
capture_resolution
laser_threshold
auto_generate_ply
save_raw_frames
scan_name
motor_step_angle
```

---

## `state.py`

Responsible for shared scanner state.

The scanner state keeps track of runtime information used by the backend and dashboard.

Example state:

```python
scanner_state = {
    "running": False,
    "status": "IDLE",
    "simulation_mode": True,
    "motor_position": 0,
    "frames_captured": 0,
    "last_capture": None,
    "point_cloud_generated": False,
    "last_point_cloud": None,
    "current_session": None,
    "last_session": None,
    "last_scan_time": None,
    "last_error": None,
    "scanner_version": "0.1"
}
```

The dashboard reads this state through:

```text
GET /status
```

---

## `status.py`

Responsible for returning scanner status data.

Current responsibility:

```text
- Provide current scanner state to the API
```

Future improvements:

```text
- Build computed status badges
- Normalize status labels
- Add hardware connection status
- Add camera availability status
- Add motor availability status
```

---

## `logger.py`

Responsible for in-memory system logs.

Current behavior:

```text
- Add timestamped log messages
- Keep recent log history
- Return logs to the dashboard
```

Example log:

```text
[16:10:21] Scan started
[16:10:22] Motor step -> 1
[16:10:23] Captured capture_step_001_...
```

Logs are displayed in the web dashboard through:

```text
GET /logs
```

---

## `config.py`

Responsible for static/default configuration values.

Examples:

```python
SCAN_STEPS = 10
STEP_DELAY = 1.0
CAPTURE_DELAY = 0.5

CAMERA_INDEX = 0

OUTPUT_FOLDER = "outputs/captures"
POINT_CLOUD_FOLDER = "outputs/point_clouds"
SCANS_FOLDER = "outputs/scans"

MAX_LOG_LINES = 100
APP_VERSION = "0.1"
```

Static config is used for default values and project paths.

Runtime settings are handled separately in:

```text
runtime_settings.py
```

---

# Web Interface Modules

## `app.py`

Main Flask application.

Responsibilities:

```text
- Define HTTP routes
- Connect frontend actions to scanner_core functions
- Serve camera stream
- Serve captured images
- Manage generated point cloud files
- Return scanner status
- Return logs
- Manage settings
- Manage simulation mode
- List scan sessions
```

Important route groups:

```text
General:
GET  /
GET  /health
GET  /api-info

Camera:
GET  /video
POST /capture
POST /clear-captures
POST /reset-camera

Scanner:
POST /start-scan
POST /stop-scan

Point Clouds:
POST /generate-ply
GET  /download-ply
GET  /point-clouds
GET  /download-ply/<filename>
POST /delete-ply/<filename>

Settings:
GET  /settings
POST /settings
GET  /simulation-mode
POST /simulation-mode

Status:
GET  /status
GET  /logs
GET  /scan-sessions
```

---

## `api_response.py`

Helper module for API responses.

Purpose:

```text
- Standardize JSON responses
- Reduce duplicate response code
- Make API behavior more predictable
```

Standard success response:

```json
{
  "success": true,
  "message": "Operation completed successfully",
  "data": {}
}
```

Standard error response:

```json
{
  "success": false,
  "message": "Error message",
  "data": {}
}
```

---

## `index.html`

Main browser dashboard.

Current sections:

```text
- Header
- Live Camera
- Scanner Status
- Scanner Controls
- Config Panel
- Generated Point Clouds
- Scan Sessions
- System Logs
```

Current frontend responsibilities:

```text
- Show live camera stream
- Trigger capture
- Clear captures
- Start and stop scan
- Generate PLY
- Download PLY
- Delete PLY
- Show logs
- Show status
- Show scan sessions
- Update settings
- Toggle simulation mode
```

---

# Runtime Flow

## Manual Capture Flow

```text
User clicks Capture Test Image
↓
POST /capture
↓
web_interface/app.py
↓
scanner_core/camera.py
↓
OpenCV reads current frame
↓
image saved to outputs/captures/
↓
scanner_state updated
↓
frontend displays thumbnail
```

---

## Manual PLY Generation Flow

```text
User clicks Generate PLY
↓
POST /generate-ply
↓
web_interface/app.py
↓
scanner_core/point_cloud.py
↓
simulated point cloud generated
↓
PLY saved to outputs/point_clouds/
↓
scanner_state updated
↓
frontend updates Generated Point Clouds
```

---

## Scan Session Flow

```text
User clicks Start Scan
↓
POST /start-scan
↓
pipeline starts in background thread
↓
session is created
↓
for each step:
    motor step
    capture image
↓
point cloud is generated
↓
session metadata is updated
↓
session is finished
```

Session output:

```text
outputs/scans/<session_id>/
├── captures/
├── point_clouds/
└── metadata.json
```

---

## Download PLY Flow

```text
User clicks Download Last PLY
↓
GET /download-ply
↓
backend checks scanner_state["last_point_cloud"]
↓
file is sent as attachment
```

Specific file download:

```text
User clicks Download in Generated Point Clouds table
↓
GET /download-ply/<filename>
↓
backend validates filename
↓
file is sent as attachment
```

---

# Simulation Mode

Simulation mode allows development without physical scanner hardware.

## Simulation Mode ON

Used when developing on PC/notebook.

Behavior:

```text
- Motor movement is simulated
- PLY generation is simulated
- GPIO is not used
- Real laser triangulation is not used
- Safe for software development
```

## Simulation Mode OFF

Reserved for Raspberry Pi hardware integration.

Future behavior:

```text
- Motor movement through GPIO
- Real camera capture
- Real laser line extraction
- Real point cloud reconstruction
```

Simulation mode is controlled by:

```text
GET  /simulation-mode
POST /simulation-mode
```

---

# Planned Hardware Integration

The current architecture is designed to allow hardware integration without rewriting the whole web interface.

Future hardware modules may include:

```text
motor_gpio.py
camera_raspberry.py
laser_detection.py
calibration.py
reconstruction.py
mesh.py
viewer.py
```

Possible future flow:

```text
Dashboard
↓
Start Scan
↓
Pipeline
↓
Motor GPIO
↓
Camera Capture
↓
Laser Detection
↓
3D Reconstruction
↓
Point Cloud
↓
Mesh Generation
↓
3D Viewer
```

---

# Current Limitations

The current software version is functional but still experimental.

Limitations:

```text
- Point cloud generation is simulated
- Motor control is simulated
- Laser line detection is not yet integrated into the web pipeline
- Camera calibration is not implemented
- Mesh generation is not implemented
- 3D viewer is not implemented
- Real GPIO hardware integration is still pending
```

---

# Design Decisions

## Why use Flask?

Flask is lightweight and easy to run on Raspberry Pi. It is enough for the current dashboard and API needs.

## Why separate `scanner_core` from `web_interface`?

To keep scanner logic independent from the web interface. This makes the project easier to maintain and easier to integrate with real hardware later.

## Why ignore `outputs/` in Git?

Generated scan files can become large and are not source code. They should stay local and not pollute the GitHub repository.

## Why use scan sessions?

Scan sessions keep each experiment organized and make the project more suitable for TCC documentation and future result comparison.

## Why keep simulation mode?

Simulation mode allows development without hardware and reduces the risk of breaking hardware-specific code while improving the software.

---

# Future Architecture Improvements

Planned improvements:

```text
- Separate simulated motor and real GPIO motor
- Add laser_detection.py
- Add calibration.py
- Add reconstruction.py
- Add mesh_generation.py
- Add 3D viewer support
- Add hardware status checks
- Add persistent configuration file
- Add automated tests
```

---

# Summary

The current architecture is designed to support gradual evolution:

```text
Current stage:
software simulation + dashboard + sessions + PLY export

Next stage:
Raspberry Pi integration + real motor + real camera

Future stage:
laser triangulation + real point cloud + mesh + 3D preview
```

This approach allows the project to grow from a working prototype into a complete low-cost 3D scanner platform.