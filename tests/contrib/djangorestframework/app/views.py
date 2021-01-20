from django.conf.urls import url, include
from django.contrib.auth.models import User
from rest_framework import viewsets, routers, serializers
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


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


class NoAuthUserViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows users to be viewed or edited.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    queryset = User.objects.all().order_by("-date_joined")
    serializer_class = UserSerializer

    # The detail argument to action specifies that this action
    # applies to the whole collection.
    # Useful resources:
    #  - https://www.django-rest-framework.org/api-guide/viewsets/#marking-extra-actions-for-routing
    #  - https://www.django-rest-framework.org/api-guide/routers/#routing-for-extra-actions
    @action(detail=False)
    def recent_users(self, request):
        recent_users = User.objects.all().order_by("-last_login")

        page = self.paginate_queryset(recent_users)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(recent_users, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def set_password(self, request, pk=None):
        # Just return a successful response.
        return Response()


router = routers.DefaultRouter()
router.register(r"users", UserViewSet)
router.register(r"no-auth-users", NoAuthUserViewSet)

# Wire up our API using automatic URL routing.
# Additionally, we include login URLs for the browsable API.
urlpatterns = [
    url(r"^", include(router.urls)),
    url(r"^api-auth/", include("rest_framework.urls", namespace="rest_framework")),
]
