import django
from django.conf.urls import include
from django.contrib.auth.models import User
from rest_framework import routers
from rest_framework import serializers
from rest_framework import viewsets


# django version < 2 does not support django.urls.re_path
# django version > 3 does not support django.conf.urls.url
if django.VERSION < (2, 0, 0):
    from django.conf.urls import url as handler
else:
    from django.urls import re_path as handler


class UserSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = User
        fields = ("url", "username", "email", "groups")


class UserViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows users to be viewed or edited.
    """

    queryset = User.objects.all().order_by("-date_joined")
    serializer_class = UserSerializer


router = routers.DefaultRouter()
router.register(r"users", UserViewSet)

# Wire up our API using automatic URL routing.
# Additionally, we include login URLs for the browsable API.
urlpatterns = [
    handler(r"^", include(router.urls)),
    handler(r"^api-auth/", include("rest_framework.urls", namespace="rest_framework")),
]
