import django

from .. import views


if django.VERSION < (2, 0, 0):
    from django.conf.urls import url
else:
    from django.urls import re_path as url

urlpatterns = [
    url(r"^$", views.index),
    url(r"^simple/$", views.BasicView.as_view()),
]
