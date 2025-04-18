import os

import django
from django.db import connection
from django.template import Context
from django.template import Template
from django.urls import path


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG = False
ROOT_URLCONF = __name__
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR,
        ],
    }
]
SECRET_KEY = ("SECRET",)
MIDDLEWARE = ["app.empty_middleware", "app.empty_middleware"]
ALLOWED_HOSTS = ["*"]
SETTINGS = dict((key, val) for key, val in locals().items() if key.isupper())


def empty_middleware(get_response):
    def middleware(request):
        try:
            response = get_response(request)
            raise ValueError("this is a middleware")
        except ValueError:
            return response

    return middleware


def index(request):
    # render a large table template
    template = Template(
        (
            "<table>\n"
            "{% for row in table %}\n"
            "<tr>{% for col in row %}<td>{{ col|escape }}</td>{% endfor %}</tr>\n"
            "{% endfor %}\n"
            "</table>"
        )
    )
    table = [range(10) for _ in range(100)]
    context = Context({"table": table})
    template.render(context)
    # query db for random data
    for _ in range(10):
        with connection.cursor() as cursor:
            cursor.execute(
                """with recursive
        cnt( id, x) as (
        values(1 , random()) union all
        select id+1,random() from cnt where id<100)
        select * from cnt"""
            )
            cursor.fetchall()
    index = Template(
        """
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Django Simple</title>
  </head>
  <body>
    <p>Hello {{name|default:"friend"}}!</p>
  </body>
</html>
    """
    )
    try:
        raise ValueError("this is an error")
    except ValueError:
        return django.http.HttpResponse(index.render(Context({})))


urlpatterns = [path("", index)]

if __name__ == "__main__":
    from django.core import management

    management.execute_from_command_line()
