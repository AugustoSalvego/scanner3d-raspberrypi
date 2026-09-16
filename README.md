# Scanner3D Raspberry Pi

Low-cost 3D laser scanner platform built with **Python**, **Flask**, **OpenCV** and **Raspberry Pi**.

This project aims to develop an experimental 3D scanner capable of capturing images from a rotating platform, organizing scan sessions, generating point cloud files and exporting results in `.ply` format.

The project is being developed as a technical portfolio project and as part of an academic research and development path.

---

## Current Project Status

The software is currently in a functional prototype stage.

The current version already includes:

- Web dashboard
- Live camera preview
- Manual image capture
- Capture management
- Scan pipeline
- Simulation mode
- Runtime scan settings
- Scanner status dashboard
- System logs
- Scan session organization
- Metadata generation
- Simulated point cloud generation
- PLY export
- PLY download
- Point cloud file listing
- Point cloud deletion
- API response standardization
- Calibration planning
- Future 3D viewer planning

The physical scanner prototype is planned to run with:

- Raspberry Pi
- USB webcam
- Red laser
- Rotating platform
- Stepper motor
- Motor driver
- External power supply

At the current stage, the software can run on a PC/notebook in simulation mode before being integrated again with the physical Raspberry Pi scanner.

---

## Main Features

### Web Interface

- Live camera stream
- Scanner controls
- Capture test image
- Clear captures
- Start scan
- Stop scan
- Generate PLY
- Download last PLY
- List generated point clouds
- Download individual PLY files
- Delete PLY files
- View scanner status
- View system logs
- Manage runtime settings
- Toggle simulation mode
- View scan sessions
- View planned future modules

### Scanner Core

- Camera abstraction
- Motor abstraction
- Scan pipeline
- Runtime settings
- Scanner state
- Logger
- Point cloud generation
- Scan session management
- Calibration status structure
- 3D viewer status structure

### Documentation

- API documentation
- Software architecture
- Calibration plan
- 3D viewer plan
- Hardware integration plan

---

## Technologies

- Python
- Flask
- OpenCV
- NumPy
- HTML
- CSS
- JavaScript
- Raspberry Pi
- Git
- GitHub

---

## Project Structure

```text
scanner3d-raspberrypi/
│
├── scanner_core/
│   ├── __init__.py
│   ├── calibration.py
│   ├── camera.py
│   ├── config.py
│   ├── logger.py
│   ├── motor.py
│   ├── pipeline.py
│   ├── point_cloud.py
│   ├── runtime_settings.py
│   ├── session.py
│   ├── state.py
│   ├── status.py
│   └── viewer.py
│
├── web_interface/
│   ├── __init__.py
│   ├── api_response.py
│   ├── app.py
│   └── templates/
│       └── index.html
│
├── tools/
│   └── development and hardware test scripts
│
├── docs/
│   ├── api.md
│   ├── software-architecture.md
│   ├── calibration-plan.md
│   ├── 3d-viewer-plan.md
│   └── hardware-integration-plan.md
│
├── outputs/
│   ├── calibration/
│   ├── captures/
│   ├── point_clouds/
│   └── scans/
│
├── README.md
├── requirements.txt
└── .gitignore
```

---

## Important Note About `outputs/`

The `outputs/` folder is used for generated files, such as:

- Captured images
- Point cloud files
- Scan session folders
- Metadata files
- Calibration files

These files are local runtime outputs and should not be committed to GitHub.

The repository should store the source code and documentation, not generated scan data.

---

## How to Run on Windows

Clone the repository:

```bash
git clone https://github.com/AugustoSalvego/scanner3d-raspberrypi.git
```

Enter the project folder:

```bash
cd scanner3d-raspberrypi
```

Create a virtual environment:

```bash
python -m venv .venv
```

Activate the virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the web interface:

```bash
python -m web_interface.app
```

Open in the browser:

```text
http://127.0.0.1:5000
```

---

## Expected `requirements.txt`

The project currently requires:

```txt
Flask
opencv-python
numpy
```

If the environment was created from another project, make sure the `requirements.txt` does not contain unrelated dependencies such as FastAPI/Uvicorn unless they are actually being used.

---

## How to Run on Raspberry Pi

After cloning the repository on the Raspberry Pi:

```bash
cd scanner3d-raspberrypi
```

Create virtual environment:

```bash
python -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the Flask server:

```bash
python -m web_interface.app
```

Then open from another device on the same network:

```text
http://<RASPBERRY_PI_IP>:5000
```

---

## Current Web Dashboard

The dashboard currently includes:

- Live Camera
- Scanner Status
- Scanner Controls
- Config Panel
- Generated Point Clouds
- Scan Sessions
- Future Modules
- System Logs

The interface is designed to support both software development on PC/notebook and future Raspberry Pi hardware integration.

---

## API Endpoints

Main available endpoints:

```text
GET  /
GET  /video
GET  /health
GET  /api-info

POST /capture
POST /clear-captures
POST /reset-camera

POST /start-scan
POST /stop-scan

POST /generate-ply
GET  /download-ply
GET  /point-clouds
GET  /download-ply/<filename>
POST /delete-ply/<filename>

GET  /settings
POST /settings

GET  /simulation-mode
POST /simulation-mode

GET  /logs
GET  /status
GET  /scan-sessions

GET  /calibration/status
GET  /viewer/status
```

Full API documentation is available in:

```text
docs/api.md
```

---

## Scan Sessions

Each scan can generate a dedicated session folder.

Example:

```text
outputs/
└── scans/
    └── scan_YYYYMMDD_HHMMSS/
        ├── captures/
        ├── point_clouds/
        └── metadata.json
```

This keeps each scan experiment organized and easier to analyze later.

A scan session may contain:

- Captured frames
- Generated point clouds
- Metadata
- Creation time
- Finish time
- Scan status
- Associated file names

This structure is important for future TCC validation and scan comparison.

---

## Simulation Mode

Simulation mode allows the software to run without physical scanner hardware.

### Simulation Mode ON

Used when running on PC/notebook.

Current behavior:

- Motor movement is simulated
- Point cloud generation is simulated
- GPIO is not used
- Safe for interface and pipeline development

### Simulation Mode OFF

Reserved for future Raspberry Pi hardware integration.

Expected future behavior:

- Real GPIO motor control
- Real camera capture
- Laser line extraction
- Real point cloud reconstruction

---

## Calibration

Calibration is planned but not fully implemented yet.

The project already includes:

- Calibration planning documentation
- Calibration status endpoint
- Basic calibration file structure
- Placeholder calibration module

Calibration areas planned:

- Camera intrinsic calibration
- Lens distortion correction
- Laser plane calibration
- Turntable center calibration
- Scale calibration

More details:

```text
docs/calibration-plan.md
```

Current endpoint:

```text
GET /calibration/status
```

---

## 3D Viewer

A browser-based 3D viewer is planned for future versions.

The viewer is not implemented yet, but the project already includes:

- 3D viewer planning documentation
- Viewer status endpoint
- Placeholder viewer module

Planned future features:

- Load generated PLY files
- Preview point clouds in the browser
- Rotate, zoom and pan the view
- Support session-based previews
- Future mesh visualization

More details:

```text
docs/3d-viewer-plan.md
```

Current endpoint:

```text
GET /viewer/status
```

---

## Documentation

Project documentation:

```text
docs/api.md
docs/software-architecture.md
docs/calibration-plan.md
docs/3d-viewer-plan.md
docs/hardware-integration-plan.md
```

### API Documentation

Explains backend routes, response patterns and endpoint behavior.

### Software Architecture

Explains the internal structure of the software, including `scanner_core`, `web_interface`, outputs, sessions and runtime flow.

### Calibration Plan

Explains future camera, laser, turntable and scale calibration.

### 3D Viewer Plan

Explains the future browser-based 3D preview system.

### Hardware Integration Plan

Explains the planned step-by-step process for integrating the software with Raspberry Pi, webcam, laser and motor hardware.

---

## Hardware Integration Roadmap

The physical scanner integration should be done gradually.

Planned order:

```text
1. Update project on Raspberry Pi
2. Run Flask server on Raspberry Pi
3. Test camera detection
4. Test live camera stream
5. Test manual capture
6. Test laser visibility
7. Test motor separately
8. Test scan pipeline with simulation mode ON
9. Test scan pipeline with real motor
10. Capture real scan sessions
11. Detect laser line
12. Generate real point cloud
13. Improve calibration
14. Improve reconstruction quality
```

The goal is to avoid testing everything at once.

---

## Current Limitations

This project is still experimental.

Current limitations:

- Point cloud generation is currently simplified/simulated
- Real laser triangulation is not implemented yet
- Camera calibration is not implemented yet
- Motor control is still simulated in the main web flow
- Browser-based 3D viewer is not implemented yet
- Mesh generation is not implemented yet
- Final hardware integration still needs testing on Raspberry Pi

---

## Future Improvements

Planned improvements:

- Real GPIO motor control
- Laser line extraction
- Real point cloud reconstruction
- Camera calibration
- Laser plane calibration
- Turntable calibration
- Scale correction
- Mesh generation
- Browser-based 3D viewer
- Session-based preview
- Improved physical scanner structure
- Multilingual interface: English, Portuguese and Italian

---

## Academic and Portfolio Goals

This project demonstrates knowledge in:

- Python development
- Flask web development
- Computer vision
- OpenCV
- Embedded systems
- Hardware/software integration
- Raspberry Pi development
- 3D reconstruction concepts
- File/session organization
- API design
- Software architecture
- Git and GitHub workflow

---

## Development Workflow

Before starting work:

```bash
git pull
```

After changes:

```bash
git status
git add .
git commit -m "Describe changes"
git push
```

Check if the working tree is clean:

```bash
git status
```

Expected result:

```text
nothing to commit, working tree clean
```

---

## Version

Current planned software milestone:

```text
Scanner3D Web Interface v0.1
```

This version focuses on:

- Dashboard
- API
- Simulation mode
- Sessions
- PLY generation
- Documentation
- Preparation for hardware integration

---

## Author

**Danilo Augusto Salvego dos Santos**

GitHub: [AugustoSalvego](https://github.com/AugustoSalvego)