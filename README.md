# Scanner3D Raspberry Pi

3D scanner platform built with Python, OpenCV and Raspberry Pi.

## Features

- Live camera streaming
- Capture system
- Point cloud generation
- PLY export
- PLY download
- Scan pipeline
- Web interface
- Logging system
- Scanner status dashboard

## Architecture

scanner_core/

- camera
- pipeline
- point cloud
- logger
- config
- state

web_interface/

- Flask application
- Dashboard
- Controls

tools/

- camera testing
- GPIO testing
- scanner debugging

outputs/

- captures
- point clouds

## Technologies

- Python
- Flask
- OpenCV
- Raspberry Pi
- Git
- GitHub

## Future Improvements

- Laser triangulation
- Real GPIO motor control
- Point cloud reconstruction
- Mesh generation
- 3D viewer
- Calibration system

## Author

Danilo Augusto Salvego dos Santos