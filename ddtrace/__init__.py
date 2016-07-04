"""Datadog Tracing client"""
from .tracer import Tracer

__version__ = '0.2.0'

# a global tracer
tracer = Tracer()
