from django.urls import re_path
from views import index
from views import shutdown_view


urlpatterns = [re_path(r"^$", index), re_path(r"^shutdown", shutdown_view, name="response-header")]
