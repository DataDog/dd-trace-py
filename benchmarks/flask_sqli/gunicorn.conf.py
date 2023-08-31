from bm.flask_utils import post_fork  # noqa
from bm.flask_utils import post_worker_init  # noqa


bind = "0.0.0.0:8000"
worker_class = "sync"
workers = 1
wsgi_app = "app:app"
pidfile = "gunicorn.pid"
