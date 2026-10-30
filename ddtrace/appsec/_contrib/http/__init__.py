"""Explicit registration for the AppSec HTTP inspection subscriber."""

from .product_subscriber import AppSecHttpSubscriber


def listen() -> None:
    AppSecHttpSubscriber.register()


def unlisten() -> None:
    AppSecHttpSubscriber.unregister()
