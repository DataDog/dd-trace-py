from django.conf.urls import url

from .. import views


urlpatterns = [
    url(r"^$", views.index),
    url(r"^simple/$", views.BasicView.as_view()),
]
