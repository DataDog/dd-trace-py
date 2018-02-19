from pylons import config
from pylons.wsgiapp import PylonsApp

from routes.middleware import RoutesMiddleware
from beaker.middleware import SessionMiddleware, CacheMiddleware

from paste.registry import RegistryManager

from .router import create_routes


def make_app(global_conf, full_stack=True, **app_conf):
    # load Pylons environment
    config.init_app(global_conf, app_conf)
    config['pylons.package'] = 'tests.contrib.pylons.app'

    # set Pylons routes
    config['routes.map'] = create_routes()

    # define a default middleware stack
    app = PylonsApp()
    app = RoutesMiddleware(app, config['routes.map'])
    app = SessionMiddleware(app, config)
    app = CacheMiddleware(app, config)
    app = RegistryManager(app)
    return app
