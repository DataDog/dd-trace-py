# Unless explicitly stated otherwise all files in this repository are licensed
# under the Apache License 2.0.
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2020 Datadog, Inc.

"""A quantile sketch with relative-error guarantees. This sketch computes
quantile values with an approximation error that is relative to the actual
quantile value. It works on both negative and non-negative input values.

For instance, using DDSketch with a relative accuracy guarantee set to 1%, if
the expected quantile value is 100, the computed quantile value is guaranteed to
be between 99 and 101. If the expected quantile value is 1000, the computed
quantile value is guaranteed to be between 990 and 1010.

DDSketch works by mapping floating-point input values to bins and counting the
number of values for each bin. The underlying structure that keeps track of bin
counts is store.

The memory size of the sketch depends on the range that is covered by the input
values: the larger that range, the more bins are needed to keep track of the
input values. As a rough estimate, if working on durations with a relative
accuracy of 2%, about 2kB (275 bins) are needed to cover values between 1
millisecond and 1 minute, and about 6kB (802 bins) to cover values between 1
nanosecond and 1 day.

The size of the sketch can be have a fail-safe upper-bound by using collapsing
stores. As shown in
<a href="http://www.vldb.org/pvldb/vol12/p2195-masson.pdf">the DDSketch paper</a>
the likelihood of a store collapsing when using the default bound is vanishingly
small for most data.

DDSketch implementations are also available in:
<a href="https://github.com/DataDog/sketches-go/">Go</a>
<a href="https://github.com/DataDog/sketches-py/">Python</a>
<a href="https://github.com/DataDog/sketches-js/">JavaScript</a>
"""

from .exception import IllegalArgumentException
from .exception import UnequalSketchParametersException
from .mapping import LogarithmicMapping
from .store import CollapsingHighestDenseStore
from .store import CollapsingLowestDenseStore
from .store import DenseStore


DEFAULT_REL_ACC = 0.01  # "alpha" in the paper
DEFAULT_BIN_LIMIT = 2048


class BaseDDSketch:
    """The base implementation of DDSketch with neither mapping nor storage specified.

    Args:
        mapping (mapping.KeyMapping): map btw values and store bins
        store (store.Store): storage for positive values
        negative_store (store.Store): storage for negative values
        zero_count (int): The count of zero values

    Attributes:
        relative_accuracy (float): the accuracy guarantee; referred to as alpha
            in the paper. (0. < alpha < 1.)

        count: the number of values seen by the sketch
        min: the minimum value seen by the sketch
        max: the maximum value seen by the sketch
        sum: the sum of the values seen by the sketch
    """

    def __init__(
        self,
        mapping,
        store,
        negative_store,
        zero_count,
    ):
        self.mapping = mapping
        self.store = store
        self.negative_store = negative_store
        self.zero_count = zero_count

        self.relative_accuracy = mapping.relative_accuracy
        self.count = self.negative_store.count + self.zero_count + self.store.count
        self.min = float("+inf")
        self.max = float("-inf")
        self._sum = 0.0

    def __repr__(self):
        return (
            f"store: {self.store}, negative_store: {self.negative_store}, "
            f"zero_count: {self.zero_count}, count: {self.count}, "
            f"sum: {self.sum}, min: {self.min}, max: {self.max}"
        )

    @property
    def name(self):
        """str: name of the sketch"""
        return "DDSketch"

    @property
    def num_values(self):
        """float: number of values in the sketch"""
        return self.count

    @property
    def avg(self):
        """float: exact avg of the values added to the sketch"""
        return self.sum / self.count

    @property
    def sum(self):
        """float: exact sum of the values added to the sketch"""
        return self._sum

    def add(self, val, weight=1.0):
        """Add a value to the sketch."""
        if weight <= 0.0:
            raise IllegalArgumentException("weight must be a positive float")

        if val > self.mapping.min_possible:
            self.store.add(self.mapping.key(val), weight)
        elif val < -self.mapping.min_possible:
            self.negative_store.add(self.mapping.key(-val), weight)
        else:
            self.zero_count += weight

        # Keep track of summary stats
        self.count += weight
        self._sum += val * weight
        if val < self.min:
            self.min = val
        if val > self.max:
            self.max = val

    def get_quantile_value(self, quantile):
        """the approximate value at the specified quantile

        Args:
            quantile (float): 0 <= q <=1

        Returns:
            the value at the specified quantile or None if the sketch is empty
        """
        if quantile < 0 or quantile > 1 or self.count == 0:
            return None

        rank = quantile * (self.count - 1)
        if rank < self.negative_store.count:
            reversed_rank = self.negative_store.count - rank - 1
            key = self.negative_store.key_at_rank(reversed_rank, lower=False)
            quantile_value = -self.mapping.value(key)
        elif rank < self.zero_count + self.negative_store.count:
            return 0
        else:
            key = self.store.key_at_rank(
                rank - self.zero_count - self.negative_store.count
            )
            quantile_value = self.mapping.value(key)
        return quantile_value

    def merge(self, sketch):
        """Merges the other sketch into this one. After this operation, this sketch
        encodes the values that were added to both this and the input sketch.
        """
        if not self.mergeable(sketch):
            raise UnequalSketchParametersException(
                "Cannot merge two DDSketches with different parameters"
            )

        if sketch.count == 0:
            return

        if self.count == 0:
            self.copy(sketch)
            return

        # Merge the stores
        self.store.merge(sketch.store)
        self.negative_store.merge(sketch.negative_store)
        self.zero_count += sketch.zero_count

        # Merge summary stats
        self.count += sketch.count
        self._sum += sketch.sum
        if sketch.min < self.min:
            self.min = sketch.min
        if sketch.max > self.max:
            self.max = sketch.max

    def mergeable(self, other):
        """Two sketches can be merged only if their gammas are equal."""
        return self.mapping.gamma == other.mapping.gamma

    def copy(self, sketch):
        """copy the input sketch into this one"""
        self.store.copy(sketch.store)
        self.negative_store.copy(sketch.negative_store)
        self.zero_count = sketch.zero_count
        self.min = sketch.min
        self.max = sketch.max
        self.count = sketch.count
        self._sum = sketch.sum


class DDSketch(BaseDDSketch):
    """The default implementation of BaseDDSketch, with optimized memory usage at
    the cost of lower ingestion speed, using an unlimited number of bins. The
    number of bins will not exceed a reasonable number unless the data is
    distributed with tails heavier than any  subexponential.
    (cf. http://www.vldb.org/pvldb/vol12/p2195-masson.pdf)
    """

    def __init__(self, relative_accuracy=None):

        # Make sure the parameters are valid
        if relative_accuracy is None:
            relative_accuracy = DEFAULT_REL_ACC

        mapping = LogarithmicMapping(relative_accuracy)
        store = DenseStore()
        negative_store = DenseStore()
        super().__init__(
            mapping=mapping, store=store, negative_store=negative_store, zero_count=0
        )


class LogCollapsingLowestDenseDDSketch(BaseDDSketch):
    """Implementation of BaseDDSketch with optimized memory usage at the cost of
    lower ingestion speed, using a limited number of bins. When the maximum
    number of bins is reached, bins with lowest indices are collapsed, which
    causes the relative accuracy to be lost on the lowest quantiles. For the
    default bin limit, collapsing is unlikely to occur unless the data is
    distributed with tails heavier than any
    subexponential. (cf. http://www.vldb.org/pvldb/vol12/p2195-masson.pdf)
    """

    def __init__(self, relative_accuracy=None, bin_limit=None):

        # Make sure the parameters are valid
        if relative_accuracy is None:
            relative_accuracy = DEFAULT_REL_ACC

        if bin_limit is None or bin_limit < 0:
            bin_limit = DEFAULT_BIN_LIMIT

        mapping = LogarithmicMapping(relative_accuracy)
        store = CollapsingLowestDenseStore(bin_limit)
        negative_store = CollapsingLowestDenseStore(bin_limit)
        super().__init__(
            mapping=mapping,
            store=store,
            negative_store=negative_store,
            zero_count=0,
        )


class LogCollapsingHighestDenseDDSketch(BaseDDSketch):
    """Implementation of BaseDDSketch with optimized memory usage at the cost of
    lower ingestion speed, using a limited number of bins. When the maximum
    number of bins is reached, bins with highest indices are collapsed, which
    causes the relative accuracy to be lost on the highest quantiles. For the
    default bin limit, collapsing is unlikely to occur unless the data is
    distributed with tails heavier than any
    subexponential. (cf. http://www.vldb.org/pvldb/vol12/p2195-masson.pdf)
    """

    def __init__(self, relative_accuracy=None, bin_limit=None):

        # Make sure the parameters are valid
        if relative_accuracy is None:
            relative_accuracy = DEFAULT_REL_ACC

        if bin_limit is None or bin_limit < 0:
            bin_limit = DEFAULT_BIN_LIMIT

        mapping = LogarithmicMapping(relative_accuracy)
        store = CollapsingHighestDenseStore(bin_limit)
        negative_store = CollapsingHighestDenseStore(bin_limit)
        super().__init__(
            mapping=mapping,
            store=store,
            negative_store=negative_store,
            zero_count=0,
        )
