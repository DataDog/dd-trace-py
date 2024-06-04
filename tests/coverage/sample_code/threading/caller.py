import threading


def call_add_in_thread():
    from .callee import called_main

    thread = threading.Thread(target=called_main, args=(1, 2))
    thread.start()
    thread.join()

def call_add_in_thread_context():
    from .callee import called_in_context

    thread = threading.Thread(target=called_in_context, args=(1, 2))
    thread.start()
    thread.join()