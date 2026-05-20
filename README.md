# Scanner3D Raspberry Pi

Low-cost 3D laser scanner platform built with **Python**, **OpenCV**, **Flask** and **Raspberry Pi**.

The goal of this project is to develop an experimental 3D scanner capable of capturing images from a rotating platform, detecting a laser line, generating point cloud data and exporting results in `.ply` format.

This project is being developed as a technical portfolio project and as part of an academic research/development path.

---

## Current Status

The project currently has a functional software prototype with:

- Web dashboard
- Live camera preview
- Test image capture
- Capture management
- Scan simulation pipeline
- Scanner status monitoring
- Log system
- Point cloud file generation
- `.ply` export
- `.ply` download
- Point cloud file management
- Simulation mode for development without hardware

The physical scanner prototype already works partially with Raspberry Pi, webcam, red laser, rotating platform and stepper motor. The current web interface is being developed and tested on a notebook before being integrated again with the Raspberry Pi hardware.

---

## Main Features

- Live camera streaming
- Image capture system
- Scan pipeline
- Simulated motor control
- Point cloud generation
- PLY export
- PLY file download
- Point cloud file listing
- Delete generated point cloud files
- Scanner status dashboard
- System logs
- Simulation mode
- Web-based control panel

---

## Technologies

- Python
- Flask
- OpenCV
- Raspberry Pi
- HTML
- CSS
- JavaScript
- Git
- GitHub

---

## Project Architecture

```text
scanner3d-raspberrypi/
│
├── scanner_core/
│   ├── camera.py
│   ├── config.py
│   ├── logger.py
│   ├── motor.py
│   ├── pipeline.py
│   ├── point_cloud.py
│   ├── state.py
│   └── status.py
│
├── web_interface/
│   ├── app.py
│   └── templates/
│       └── index.html
│
├── tools/
│   ├── camera tests
│   ├── GPIO tests
│   └── debugging scripts
│
├── outputs/
│   ├── captures/
│   └── point_clouds/
│
├── docs/
├── requirements.txt
├── README.md
└── .gitignore
```

---

## Architecture Overview

The project is organized into separate modules to avoid mixing hardware logic, scanner logic and web interface code.

### `scanner_core/`

Contains the core scanner logic:

- Camera handling
- Scan pipeline
- Motor abstraction
- Point cloud generation
- Runtime state
- Logs
- Configuration

### `web_interface/`

Contains the Flask web application:

- Dashboard
- Control buttons
- Status display
- Logs
- Point cloud file management

### `tools/`

Contains testing and debugging scripts used during development.

### `outputs/`

Stores generated files such as captured images and point cloud files.

Generated outputs are ignored by Git to avoid storing temporary scan data in the repository.

---

## How to Run

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

Activate the virtual environment on Windows:

```bash
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

## Web Interface

The current dashboard includes:

- Live camera preview
- Capture test image
- Clear captures
- Start scan
- Stop scan
- Generate PLY
- Download last PLY
- List generated point clouds
- Download individual PLY files
- Delete PLY files
- Scanner status
- System logs
- Simulation mode

---

## API Endpoints

Main endpoints currently available:

```text
GET  /
GET  /video
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

GET  /logs
GET  /status
GET  /health
GET  /api-info
```

---

## Current Limitations

This is still an experimental prototype.

Current limitations:

- Point cloud generation is still simplified/simulated in the web version
- Real laser triangulation is not fully implemented yet
- Camera calibration still needs improvement
- Motor control is currently simulated in the web interface
- The notebook version is used for software development only
- Final hardware integration will be tested on Raspberry Pi

---

## Hardware Prototype

The physical prototype uses:

- Raspberry Pi 3
- USB webcam
- Red laser
- Rotating platform
- Stepper motor
- External power supply
- Low-cost physical structure

The scanner already demonstrated initial point cloud generation from captured images, but the result still requires filtering, calibration and better optical quality.

---

## Roadmap

Planned improvements:

- Real GPIO motor integration
- Laser line extraction improvement
- Camera calibration
- Laser triangulation
- Real point cloud reconstruction
- Mesh generation
- 3D viewer in the browser
- Better scan configuration panel
- Multilingual interface: English, Portuguese and Italian
- Improved documentation for academic presentation
- Hardware integration with Raspberry Pi
- Cleaner and more robust physical structure

---

## Academic and Portfolio Goals

This project aims to demonstrate knowledge in:

- Computer vision
- Embedded systems
- Python development
- Web interface development
- Hardware/software integration
- 3D reconstruction concepts
- Software architecture
- Version control with Git and GitHub

---

## Author

**Danilo Augusto Salvego dos Santos**

GitHub: [AugustoSalvego](https://github.com/AugustoSalvego)
