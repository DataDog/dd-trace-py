import logging


def post_fork(server, worker):
    """Log the startup time of each worker."""
    logging.info("Worker %s started", worker.pid)


def post_worker_init(worker):
    logging.info("Worker %s initialized", worker.pid)


logconfig_dict = {
    "version": 1,
    "formatters": {
        "default": {
            "format": "[%(asctime)s.%(msecs)03d +0000] [%(process)d] [%(levelname)s] %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
    },
    "loggers": {
        "": {  # root logger
            "handlers": ["console"],
            "level": "INFO",
        },
        "gunicorn.error": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "gunicorn.access": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {
        "level": "INFO",
        "handlers": ["console"],
    },
}
