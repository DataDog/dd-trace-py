from starlette.routing import Match


def set_routes(app_routes):
    global routes
    routes = app_routes


def get_resource(scope):
    path = None
    global routes
    for route in routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            path = route.path
            break
        elif match == Match.PARTIAL and path is None:
            path = route.path

    return path
