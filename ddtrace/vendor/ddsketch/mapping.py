# Unless explicitly stated otherwise all files in this repository are licensed
# under the Apache License 2.0.
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2020 Datadog, Inc.

"""A mapping between values and integer indices that imposes relative accuracy
guarantees. Specifically, for any value `minIndexableValue() < value <
maxIndexableValue` implementations of `KeyMapping` must be such that
`value(key(v))` is close to `v` with a relative error that is less than
`relative_accuracy`.

In implementations of KeyMapping, there is generally a trade-off between the
cost of computing the key and the number of keys that are required to cover a
given range of values (memory optimality). The most memory-optimal mapping is
the LogarithmicMapping, but it requires the costly evaluation of the logarithm
when computing the index. Other mappings can approximate the logarithmic
mapping, while being less computationally costly.
"""
from abc import ABC
from abc import abstractmethod
import math
import sys

from .exception import IllegalArgumentException


class KeyMapping(ABC):
    """
    Args:
        relative_accuracy (float): the accuracy guarantee; referred to as alpha
            in the paper. (0. < alpha < 1.)
        offset (float): an offset that can be used to shift all bin keys
    Attributes:
        gamma (float): the base for the exponential buckets
            gamma = (1 + alpha) / (1 - alpha)
        min_possible: the smallest value the sketch can distinguish from 0
        max_possible: the largest value the sketch can handle
        _multiplier (float): used for calculating log_gamma(value)
            initially, _multiplier = 1 / log(gamma)
    """

    def __init__(self, relative_accuracy, offset=0.0):
        if relative_accuracy <= 0 or relative_accuracy >= 1:
            raise IllegalArgumentException("Relative accuracy must be between 0 and 1.")
        self.relative_accuracy = relative_accuracy
        self._offset = offset

        gamma_mantissa = 2 * relative_accuracy / (1 - relative_accuracy)
        self.gamma = 1 + gamma_mantissa
        self._multiplier = 1 / math.log1p(gamma_mantissa)
        self.min_possible = sys.float_info.min * self.gamma
        self.max_possible = sys.float_info.max / self.gamma

    @classmethod
    def from_gamma_offset(cls, gamma, offset):
        """Constructor used by pb.proto"""
        relative_accuracy = (gamma - 1.0) / (gamma + 1.0)
        return cls(relative_accuracy, offset=offset)

    @abstractmethod
    def _log_gamma(self, value):
        """Return (an approximation of) the logarithm of the value base gamma"""

    @abstractmethod
    def _pow_gamma(self, value):
        """Return (an approximation of) gamma to the power value"""

    def key(self, value):
        """
        Args:
            value (float)
        Returns:
            int: the key specifying the bucket for value
        """
        return int(math.ceil(self._log_gamma(value)) + self._offset)

    def value(self, key):
        """
        Args:
            key (int)
        Returns:
            float: the value represented by the bucket specified by the key
        """
        return self._pow_gamma(key - self._offset) * (2.0 / (1 + self.gamma))


class LogarithmicMapping(KeyMapping):
    """A memory-optimal KeyMapping, i.e., given a targeted relative accuracy, it
    requires the least number of keys to cover a given range of values. This is
    done by logarithmically mapping floating-point values to integers.
    """

    def __init__(self, relative_accuracy, offset=0.0):
        super().__init__(relative_accuracy, offset=offset)
        self._multiplier *= math.log(2)

    def _log_gamma(self, value):
        return math.log2(value) * self._multiplier

    def _pow_gamma(self, value):
        return 2 ** (value / self._multiplier)


def _cbrt(x):
    # type: (float) -> float
    y = abs(x) ** (1.0 / 3.0)
    if x < 0:
        return -y
    return y


class LinearlyInterpolatedMapping(KeyMapping):
    """A fast KeyMapping that approximates the memory-optimal one
    (LogarithmicMapping) by extracting the floor value of the logarithm to the
     base 2 from the binary representations of floating-point values and
    linearly interpolating the logarithm in-between."""

    def _log2_approx(self, value):
        """approximates log2 by s + f
        where v = (s+1) * 2 ** f  for s in [0, 1)

        frexp(v) returns m and e s.t.
        v = m * 2 ** e ; (m in [0.5, 1) or 0.0)
        so we adjust m and e accordingly
        """
        mantissa, exponent = math.frexp(value)
        significand = 2 * mantissa - 1
        return significand + (exponent - 1)

    def _exp2_approx(self, value):
        """inverse of _log2_approx"""
        exponent = math.floor(value) + 1
        mantissa = (value - exponent + 2) / 2.0
        return math.ldexp(mantissa, exponent)

    def _log_gamma(self, value):
        return self._log2_approx(value) * self._multiplier

    def _pow_gamma(self, value):
        return self._exp2_approx(value / self._multiplier)


class CubicallyInterpolatedMapping(KeyMapping):
    """A fast KeyMapping that approximates the memory-optimal LogarithmicMapping by
     extracting the floor value of the logarithm to the base 2 from the binary
     representations of floating-point values and cubically interpolating the
     logarithm in-between.

    More detailed documentation of this method can be found in:
    <a href="https://github.com/DataDog/sketches-java/">sketches-java</a>
    """

    A = 6 / 35
    B = -3 / 5
    C = 10 / 7

    def __init__(self, relative_accuracy, offset=0.0):
        super().__init__(relative_accuracy, offset=offset)
        self._multiplier /= self.C

    def _cubic_log2_approx(self, value):
        """approximates log2 using a cubic polynomial"""
        mantissa, exponent = math.frexp(value)
        significand = 2 * mantissa - 1
        return (
            (self.A * significand + self.B) * significand + self.C
        ) * significand + (exponent - 1)

    def _cubic_exp2_approx(self, value):
        """Derived from Cardano's formula"""

        exponent = math.floor(value)
        delta_0 = self.B * self.B - 3 * self.A * self.C
        delta_1 = (
            2 * self.B * self.B * self.B
            - 9 * self.A * self.B * self.C
            - 27 * self.A * self.A * (value - exponent)
        )
        cardano = _cbrt(
            (delta_1 - ((delta_1 * delta_1 - 4 * delta_0 * delta_0 * delta_0) ** 0.5))
            / 2
        )
        significand_plus_one = (
            -(self.B + cardano + delta_0 / cardano) / (3 * self.A) + 1
        )
        mantissa = significand_plus_one / 2
        return math.ldexp(mantissa, exponent + 1)

    def _log_gamma(self, value):
        return self._cubic_log2_approx(value) * self._multiplier

    def _pow_gamma(self, value):
        return self._cubic_exp2_approx(value / self._multiplier)
