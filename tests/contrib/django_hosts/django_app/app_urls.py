import django

from .. import views


if django.VERSION < (4, 0, 0):
    from django.conf.urls import url as handler
else:
    from django.urls import re_path as handler


urlpatterns = [
    handler(r"^$", views.index),
    handler(r"^simple/$", views.BasicView.as_view()),
]
