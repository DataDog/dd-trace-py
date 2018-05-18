from webob import Request, Response

class ExceptionToSuccessMiddleware(object):
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        req = Request(environ)
        try:
            response = req.get_response(self.app)
        except Exception:
            response = Response()
            response.status_int = 200
            response.body = 'An error has been handled appropriately'
        return response(environ, start_response)


class ExceptionToClientErrorMiddleware(object):
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        req = Request(environ)
        try:
            response = req.get_response(self.app)
        except Exception:
            response = Response()
            response.status_int = 404
            response.body = 'An error has occured with proper client error handling'
        return response(environ, start_response)
