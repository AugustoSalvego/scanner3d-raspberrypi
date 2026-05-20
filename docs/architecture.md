# Architecture

## Layers

Web Interface

- Flask
- Dashboard
- Scanner controls

Core

- Camera
- Motor
- Pipeline
- Point cloud generation

Outputs

- Captures
- PLY files

Tools

- Debug tools
- Hardware tests

## Flow

Web Interface

↓

Pipeline

↓

Camera Capture

↓

Point Cloud

↓

PLY Export

↓

Download