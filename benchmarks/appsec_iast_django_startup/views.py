import django
from django.template import Context
from django.template import Template


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
    return django.http.HttpResponse(index.render(Context({})))
