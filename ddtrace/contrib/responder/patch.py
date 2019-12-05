
# stdlib

# project
import ddtrace
from ddtrace.ext import http
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ddtrace.utils.wrappers import unwrap as _u
from ddtrace.propagation.http import extract_context_from_http_headers

# 3p
import responder
patches = [] # will be overriden when patched funcs are defined.


def patch():
    if _is_patched(responder):
        return
    _set_patched(responder, True)

    for obj, mname, fname, func in patches:
        _w(mname, fname, func)

    print('patched')

def unpatch():
    if not _is_patched(responder):
        return
    _set_patched(responder, False)

    for obj, _, prop, _ in patches:
        if '.' in prop:
            attr, _, prop = prop.partition('.')
            obj = getattr(obj, attr, object())
        _u(obj, prop)

    print('unpatched')





async def _api_call(wrapped, instance, args, kwargs):
    tracer = _get_tracer(instance)
    assert tracer

    scope, receive, send = args # FIXME: what if this is partially using kwargs?

    # check for propagated traced ids in the http heaers.
    headers = {k.decode():v for k,v in scope.get('headers', ())}
    ctx = extract_context_from_http_headers(headers)
    if ctx.trace_id:
        tracer.context_provider.activate(ctx)

    with tracer.trace("responder.request", service="responder") as span:
        traced_send = _trace_asgi_send_func(send, span)
        span.set_tag(http.METHOD, scope.get('method'))
        await wrapped(scope, receive, traced_send)

patches = [
    (responder.api, 'responder.api', 'API.__call__', _api_call)
]


def _trace_asgi_send_func(send, span):
    async def _traced_send(event):
        event_type = event.get("type")
        if event_type == "http.response.start":
            span.set_tag(http.STATUS_CODE, event.get("status"))
        await send(event)
    return _traced_send

def _get_tracer(instance):
    pin = ddtrace.Pin.get_from(instance)
    if pin:
        return pin.tracer
    return ddtrace.tracer

# misc

def _set_patched(obj, state):
    setattr(obj, 'datadog_patch', state)

def _is_patched(obj):
    return getattr(obj, 'datadog_patch', False)



if __name__ == '__main__':
    patch()

    api = responder.API()

    @api.route("/login")
    def login(req, resp):
        resp.text = "asdf"

    @api.route("/exception")
    def exception(req, resp):
        raise FakeError("ohno")

    print(api.session().get("/login"))

    unpatch()


    print(api.session().get("/login"))



