from flask import jsonify


def api_success(message="Success", data=None, **extra):
    response = {
        "success": True,
        "message": message,
        "data": data or {}
    }

    response.update(extra)

    return jsonify(response)


def api_error(message="Error", status_code=400, data=None, **extra):
    response = {
        "success": False,
        "message": message,
        "data": data or {}
    }

    response.update(extra)

    return jsonify(response), status_code