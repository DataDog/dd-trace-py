import os

from flask_taint_sinks_views import create_app

from ddtrace import auto  # noqa: F401


port = int(os.getenv("FLASK_RUN_PORT", 8000))

app = create_app()

if __name__ == "__main__":
    app.run(debug=False, port=port)
