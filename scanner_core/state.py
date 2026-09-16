"""Compatibility access. Use controller.status() for a synchronized snapshot."""
from scanner_core.pipeline import controller
scanner_state = controller.state
