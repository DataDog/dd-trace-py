from starlette.routing import Match


def get_resource(scope, routes):
    path = None
    for route in routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            path = route.path
            break
        elif match == Match.PARTIAL and path is None:
            path = route.path

    return path
