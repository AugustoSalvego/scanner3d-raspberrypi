# Laser Diagnostics Guide

This document explains the current laser diagnostic tools used by the Scanner3D Raspberry Pi project.

This stage is focused on understanding and validating the laser detection, not on final 3D reconstruction yet.

---

## Current Stage

The project currently has:

- Live camera preview
- Laser overlay preview
- Laser mask preview
- Laser diagnostic endpoint
- Background calibration for the laser
- Visual feedback after diagnostic actions

This is still a diagnostic stage.

The system does not yet perform real 3D reconstruction from the laser line.

---

## Important Decision

Camera calibration will be done later.

Reason:

- The lighting setup is not final yet
- A green light may be added later
- Camera position may still change
- Laser position may still change
- The scanner box/environment may still change

Camera calibration should only be performed after the physical setup is stable.

---

## Buttons and Expected Behavior

### Live Camera

Shows the normal camera stream.

Use it to check:

- If the camera is working
- If the object is visible
- If the laser is visible
- If the platform is centered
- If the image is too dark or overexposed

---

### Laser Overlay

Shows the camera image with detected laser pixels highlighted.

Use it to check:

- Where the system thinks the laser is
- Whether the laser is being detected on the object
- Whether the wall, platform, or reflections are being detected incorrectly

---

### Laser Mask

Shows a black and white image.

White pixels mean:

- The system detected possible laser pixels

Black pixels mean:

- The system ignored that area

Use it to check:

- If the detected laser is thin and clean
- If there is too much noise
- If reflections are being detected
- If the object laser line is visible

---

### Laser Diagnostic

Returns numbers about the current laser detection.

Important values:

- Laser pixels
- Coverage percentage
- Image resolution
- Background calibration status

High coverage may indicate too much noise or reflection.

Very low coverage may indicate that the laser is not being detected well.

---

### Calibrate Background

This captures the laser/background without the object.

Correct procedure:

1. Keep the camera fixed
2. Keep the laser fixed
3. Keep the lighting fixed
4. Remove the object from the platform
5. Keep the laser on
6. Click Calibrate Background
7. Put the object back
8. Test Laser Mask again

This calibration is only for removing fixed background laser/reflection.

It is not the final geometric scanner calibration.

---

## What Danilo Should Analyze

Danilo should analyze the physical result:

- Is the laser visible on the object?
- Is the laser line thin or too thick?
- Is the camera image clean?
- Is the green light helping or hurting?
- Is the platform reflection strong?
- Is the object inside the useful camera area?
- Did the mask detect the object or only the background?
- Did the background calibration improve the result?

Screenshots are important.

Useful screenshots:

- Live Camera
- Laser Overlay
- Laser Mask
- Laser Diagnostic values

---

## What the AI Should Analyze

The AI should analyze the software side:

- Code structure
- Git changes
- Possible bugs
- Repeated code
- Missing feedback messages
- Missing validation
- Whether the current algorithm is too simple
- What should be implemented next
- What should not be implemented yet

The AI should not add large changes without first explaining the purpose.

---

## Current Limitations

The current system does not yet have:

- Real camera calibration
- Lens distortion correction
- Laser plane calibration
- Turntable center calibration
- Real 3D triangulation
- Real point cloud from detected laser line
- Motor GPIO integration in the main pipeline

These steps will be implemented later, one by one.

---

## Safe Development Rule

From now on:

1. One change at a time
2. One test at a time
3. One commit at a time
4. No `git add .` unless absolutely necessary
5. Always run `git status` before and after changes
6. Keep generated files out of GitHub

---

## Next Safe Technical Steps

Recommended order:

1. Test current diagnostics with stable lighting
2. Add or improve user feedback messages
3. Clean duplicated JavaScript functions
4. Add a visible background calibration status
5. Add laser centerline detection
6. Only later start geometric calibration and real 3D reconstruction
