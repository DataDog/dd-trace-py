import threading
import time


class Timer(threading.Thread):

    """Class that takes for input : time_loop (float or int) and a function.
    Every time_loop, the thread calls the callable and then sleep for another time_loop

    Attributes:
        _timeLoop: input int or float
        _event: input event
        _stop: threading.Event allowing thread to join"""

    def __init__(self, time_loop, event):

        """Initialise Timer"""

        super(Timer, self).__init__()
        self._timeLoop = time_loop
        self._event = event
        self._stop = threading.Event()

    def run(self):

        """Thread sleeps for _timeLoop and then calls the function call"""

        while not self._stop.isSet():
            time.sleep(self._timeLoop)
            self._event.set()

    def join(self, timeout=None):

        """Terminates thread"""

        self._stop.set()
        super(Timer, self).join(timeout)
