from ddtrace.trace import tracer


class ServiceRenamingMiddleware:
    """
    If the request carries `X-Rename-Service: true`, rewrite the current
    Datadog root span’s service name to “sub-service” and tag it.
    """

    def __init__(self, get_response):
        self.get_response = get_response  # standard Django hook

    def __call__(self, request):
        # ---- before-view logic (runs on the way in) -----------------
        if request.headers.get("X-Rename-Service", "false").lower() == "true":
            service_name = "sub-service"
            root_span = tracer.current_root_span()
            if root_span is not None:
                root_span.service = service_name
                root_span.set_tag("scope", service_name)

        # ---- call the view / downstream middleware ------------------
        response = self.get_response(request)
        return response
