import os
from threading import Thread
import threading


def child():
    print("I'm in the child, process_id =", os.getpid())
    result = 1
    # Run until parent exits
    while os.getppid() != 1:
        result *= result + 1

def parent():
    print("I'm in the parent, thread_id=", threading.get_ident())
    result = 1
    while True:
        result = pow(result, result)

def main():
    Thread(target=parent, daemon=True).start()
    Thread(target=parent, daemon=True).start()
    Thread(target=parent, daemon=True).start()

    pid = os.fork()
    if pid == 0:
        return child()

    return parent()

if __name__ == "__main__":
    print("Parent process id =", os.getpid())
    try:
        main()
    except KeyboardInterrupt:
        os._exit(1)