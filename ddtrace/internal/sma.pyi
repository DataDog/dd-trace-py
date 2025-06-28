class SimpleMovingAverage:
    """
    Simple Moving Average implementation.
    """

    def __init__(self, size: int) -> None:
        """
        :param size: The size of the window to calculate the moving average.
        :type size: :obj:`int`
        """
        ...
    def get(self) -> float:
        """
        Get the current moving average value.

        :return: The moving average as a float.
        :rtype: float
        """
        ...
    def set(self, count: int, total: int) -> None:
        """
        Set the value of the next bucket and update the SMA value.

        :param count: The valid quantity of the next bucket.
        :type count: :obj:`int`
        :param total: The total quantity of the next bucket.
        :type total: :obj:`int`
        """
        ...
    @property
    def current_average(self) -> float:
        """Get current average (read-only property)"""
        ...
