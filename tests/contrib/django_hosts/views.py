"""
Class based views used for Django tests.
"""
from django.http import HttpResponse
from django.views.generic import View


class BasicView(View):
    def get(self, request):
        return HttpResponse("")

    def post(self, request):
        return HttpResponse("")

    def delete(self, request):
        return HttpResponse("")

    def head(self, request):
        return HttpResponse("")


def index(request):
    return HttpResponse("Hello, test app.")
