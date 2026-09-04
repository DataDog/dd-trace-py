from ddtrace.appsec._contrib.subprocess.subscribers import AppSecSubprocessCommandSubscriber


def listen() -> None:
    AppSecSubprocessCommandSubscriber.register()


def unlisten() -> None:
    AppSecSubprocessCommandSubscriber.unregister()
