from bm.flask_utils import post_fork  # noqag
from bm.flask_utils import post_worker_init  # noqa


bind = "0.0.0.0:8000"
worker_class = "sync"
workers = 4
wsgi_app = "app:app"
pidfile = "gunicorn.pid"
