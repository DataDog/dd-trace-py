import flask

from ddtrace import Pin


def get_current_app():
    """Helper to get the flask.app.Flask from the current app context"""
    try:
        return flask.current_app
    except RuntimeError:
        # raised if current_app is None: https://github.com/pallets/flask/blob/2.1.3/src/flask/globals.py#L40
        pass
    return None
