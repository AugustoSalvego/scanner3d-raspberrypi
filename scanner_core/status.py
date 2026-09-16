def get_status():
    from scanner_core.pipeline import controller
    return controller.status()

def get_status_badge(state):
    return state.get("phase", "idle").upper()
