import itertools
import threading


class Counter(object):
    """Thread safe counter class, keeps count of things

    ::

        from ddtrace.utils.counter import Counter

        counter = Counter()

        counter.increment()
        counter.value()  # 1

        for _ in range(10):
            counter.increment()
        counter.value()  # 11

        counter.decrement()
        counter.value()  # 10
    """

    def __init__(self):
        self._read_count = 0
        self._increment = itertools.count()
        self._decrement = itertools.count()
        self._read_lock = threading.Lock()

    def increment(self):
        """Increment the counter"""
        next(self._increment)

    def decrement(self):
        """Increment the counter"""
        next(self._decrement)

    def _value(self):
        """Return the current value of the counter"""
        inc = next(self._increment) - self._read_count
        dec = next(self._decrement) - self._read_count
        self._read_count += 1
        return inc - dec

    def value(self, no_lock=False):
        """
        Return the current value of the counter

        :param no_lock: Pass in :obj:`True` if you have acquired your own lock (default: :obj:`False`)
        :type no_lock: :obj:`bool`
        :rtype: :obj:`int`
        :returns: The current value of this counter
        """
        if no_lock:
            return self._value()

        with self._read_lock:
            return self._value()
