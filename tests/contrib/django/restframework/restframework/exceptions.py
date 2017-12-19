from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework.exceptions import APIException
from rest_framework import status


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    # Custom 500 error handler
    if response is not None:
        return Response({'detail': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return response
