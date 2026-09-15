from ddtrace.appsec._contrib.dbapi.subscribers import AppSecDbApiSubscriber


def listen() -> None:
    AppSecDbApiSubscriber.register()


def unlisten() -> None:
    AppSecDbApiSubscriber.unregister()
