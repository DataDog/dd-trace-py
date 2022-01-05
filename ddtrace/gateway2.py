from enum import Enum


class EVENTS(Enum):
    INCOMING_REQUEST_START = 0
    INCOMING_REQUEST_UPDATE = 1
    INCOMING_REQUEST_END = 2


class Gateway2(object):
    def __init__(self):
        self.listeners = {}

    def on(self, event, callback):
        if event not in self.listeners:
            self.listeners[event] = set()
        self.listeners[event].add(callback)

    def emit(self, event, data):
        if event not in self.listeners:
            return
        return map(lambda cb: cb(data), self.listeners[event])
