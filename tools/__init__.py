"""Command line tools for the Scanner3D.

Run them from the project root as modules::

    python -m tools.test_camera        # camera detection and real settings
    python -m tools.test_motor         # move the turntable a small amount
    python -m tools.test_laser         # laser power control
    python -m tools.test_gpio          # blink the four ULN2003 pins
    python -m tools.detect_laser       # laser line detection diagnostics
    python -m tools.calibrate          # camera / laser plane / turntable
    python -m tools.run_scan           # run a scan without the web interface
    python -m tools.reconstruct        # rebuild a cloud from stored captures

The hardware tools are safe to run on a development machine: they report that
gpiozero or a camera is unavailable instead of failing obscurely.
"""
