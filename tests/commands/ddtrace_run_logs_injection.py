from io import StringIO
import json
import logging


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs log records as JSON."""

    def format(self, record):
        return json.dumps({k: str(v) for k, v in record.__dict__.items()})


if __name__ == "__main__":
    log_capture_string = StringIO()
    handler = logging.StreamHandler(log_capture_string)
    handler.setLevel(logging.INFO)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("ddtrace")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("Test log message")

    json_contents = json.loads(log_capture_string.getvalue().strip())
    assert json_contents.get("dd.service") == "my-service"
    assert json_contents.get("dd.env") == "my-env"
    assert json_contents.get("dd.version") == "my-version"
    print("Test success")
