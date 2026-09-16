"""Flask blueprints, one per area of the API."""

from web_interface.routes import calibration, camera, clouds, core, scan, settings

BLUEPRINTS = (
    core.blueprint,
    camera.blueprint,
    scan.blueprint,
    clouds.blueprint,
    settings.blueprint,
    calibration.blueprint,
)

__all__ = ["BLUEPRINTS"]
