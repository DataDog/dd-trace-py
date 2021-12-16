from django.urls import re_path

from .. import views


urlpatterns = [
    re_path(r"^$", views.index),
    re_path(r"^simple/$", views.BasicView.as_view()),
]
