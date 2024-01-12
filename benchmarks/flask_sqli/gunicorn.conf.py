from bm.flask_utils import post_fork  # noqa:I001,F401
from bm.flask_utils import post_worker_init  # noqa:F401

bind = "0.0.0.0:8000"
worker_class = "sync"
workers = 1
wsgi_app = "app:app"
pidfile = "gunicorn.pid"
