def get_viewer_status():
    return {
        "viewer_available": True, "point_cloud_preview": True, "mesh_preview": False,
        "supported_formats": ["ply", "png"],
        "notes": "Inspect the multi-view preview and detection overlays for each session.",
    }
