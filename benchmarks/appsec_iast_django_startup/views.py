from django.http import HttpResponse
from django.template import Context
from django.template import Template

from ddtrace import tracer


def index(request):
    # render a large table template
    index = Template(
        """
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Django Simple</title>
  </head>
  <body>
    <p>Hello</p>
  </body>
</html>
    """
    )
    return HttpResponse(index.render(Context({})))


def shutdown_view(request):
    if hasattr(tracer, "_span_aggregator"):
        writer = tracer._span_aggregator.writer
    else:
        writer = tracer._writer
    writer.flush_queue()
    return HttpResponse("SHUTDOWN")
