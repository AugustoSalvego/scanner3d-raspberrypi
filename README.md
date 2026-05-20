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
