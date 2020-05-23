import pymongo

from ddtrace import Pin

from ...ext import mongo as mongox
from ...utils.deprecation import deprecated
from .client import TracedMongoClient

# Original Client class
_MongoClient = pymongo.MongoClient


def patch():
    setattr(pymongo, 'MongoClient', TracedMongoClient)


def unpatch():
    setattr(pymongo, 'MongoClient', _MongoClient)


@deprecated(message='Use patching instead (see the docs).', version='1.0.0')
def trace_mongo_client(client, tracer, service=mongox.SERVICE):
    traced_client = TracedMongoClient(client)
    Pin(service=service, tracer=tracer).onto(traced_client)
    return traced_client
