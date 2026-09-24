import falcon

from ddtrace.internal.utils.version import parse_version


FALCON_VERSION = parse_version(falcon.__version__)
TEXT_ATTR = "text" if FALCON_VERSION >= (3, 0, 0) else "body"


class Resource200:
    """Throw a handled exception here to ensure our use of
    set_traceback() doesn't affect 200s
    """

    def on_get(self, req, resp, **kwargs):
        try:
            1 / 0
        except Exception:
            pass

        resp.status = falcon.HTTP_200
        setattr(resp, TEXT_ATTR, "Success")
        resp.append_header("my-response-header", "my_response_value")


class DynamicURIResource:
    def on_get(self, req, resp, name):
        resp.status = falcon.HTTP_200
        setattr(resp, TEXT_ATTR, name)


class Resource201:
    def on_post(self, req, resp, **kwargs):
        resp.status = falcon.HTTP_201
        setattr(resp, TEXT_ATTR, "Success")


class Resource500:
    def on_get(self, req, resp, **kwargs):
        resp.status = falcon.HTTP_500
        setattr(resp, TEXT_ATTR, "Failure")


class ResourceException:
    def on_get(self, req, resp, **kwargs):
        raise Exception("Ouch!")


class ResourceNotFound:
    def on_get(self, req, resp, **kwargs):
        # simulate that the endpoint is hit but raise a 404 because
        # the object isn't found in the database
        raise falcon.HTTPNotFound()
