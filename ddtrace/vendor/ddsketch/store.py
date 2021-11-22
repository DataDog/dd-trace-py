# Unless explicitly stated otherwise all files in this repository are licensed
# under the Apache License 2.0.
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2020 Datadog, Inc.

"""Stores map integers to counters. They can be seen as a collection of bins.
We start with 128 bins and grow the store in chunks of 128 unless specified
otherwise."""

from abc import ABC
from abc import abstractmethod
import math


CHUNK_SIZE = 128


class Store(ABC):
    """The basic specification of a store

    Attributes:
        count (float): the sum of the counts for the bins
        min_key (int): the minimum key bin
        max_key (int): the maximum key bin
    """

    def __init__(self):
        self.count = 0
        self.min_key = float("+inf")
        self.max_key = float("-inf")

    @abstractmethod
    def copy(self, store):
        """copy the input store into this one"""

    @abstractmethod
    def length(self):
        """the number of bins"""

    @abstractmethod
    def add(self, key, weight=1.0):
        """Updates the counter at the specified index key, growing the number of bins if
        necessary."""

    @abstractmethod
    def key_at_rank(self, rank, lower=True):
        """Return the key for the value at given rank.

        E.g., if the non-zero bins are [1, 1] for keys a, b with no offset

        if lower = True:
             key_at_rank(x) = a for x in [0, 1)
             key_at_rank(x) = b for x in [1, 2)

        if lower = False:
             key_at_rank(x) = a for x in (-1, 0]
             key_at_rank(x) = b for x in (0, 1]
        """

    @abstractmethod
    def merge(self, store):
        """Merge another store into this one. This should be equivalent as running the
        add operations that have *been run on the other store on this one.
        """


class DenseStore(Store):
    """A dense store that keeps all the bins between the bin for the min_key and the
    bin for the max_key.

    Args:
        chunk_size (int, optional): the number of bins to grow by

    Attributes:
        count (int): the sum of the counts for the bins
        min_key (int): the minimum key bin
        max_key (int): the maximum key bin
        offset (int): the difference btw the keys and the index in which they are stored
        bins (List[int]): the bins
    """

    def __init__(self, chunk_size=CHUNK_SIZE):
        super().__init__()

        self.chunk_size = chunk_size
        self.offset = 0
        self.bins = []

    def __repr__(self):
        repr_str = "{"
        for i, sbin in enumerate(self.bins):
            repr_str += f"{i+self.offset}: {sbin}, "
        repr_str += (
            f"}}, min_key:{self.min_key}, max_key:{self.max_key}, offset:{self.offset}"
        )
        return repr_str

    def copy(self, store):
        self.bins = store.bins[:]
        self.count = store.count
        self.min_key = store.min_key
        self.max_key = store.max_key
        self.offset = store.offset

    def length(self):
        """the number of bins"""
        return len(self.bins)

    def add(self, key, weight=1.0):
        idx = self._get_index(key)
        self.bins[idx] += weight
        self.count += weight

    def _get_index(self, key):
        """calculate the bin index for the key, extending the range if necessary"""

        if key < self.min_key:
            self._extend_range(key)
        elif key > self.max_key:
            self._extend_range(key)

        return key - self.offset

    def _get_new_length(self, new_min_key, new_max_key):
        desired_length = new_max_key - new_min_key + 1
        return self.chunk_size * math.ceil((desired_length) / self.chunk_size)

    def _extend_range(self, key, second_key=None):
        """Grow the bins as necessary and call _adjust"""
        second_key = second_key or key
        new_min_key = min(key, second_key, self.min_key)
        new_max_key = max(key, second_key, self.max_key)

        if self.length() == 0:
            # initialize bins
            self.bins = [0] * self._get_new_length(new_min_key, new_max_key)
            self.offset = new_min_key
            self._adjust(new_min_key, new_max_key)

        elif new_min_key >= self.min_key and new_max_key < self.offset + self.length():
            # no need to change the range; just update min/max keys
            self.min_key = new_min_key
            self.max_key = new_max_key

        else:
            # grow the bins
            new_length = self._get_new_length(new_min_key, new_max_key)
            if new_length > self.length():
                self.bins.extend([0] * (new_length - self.length()))
            self._adjust(new_min_key, new_max_key)

    def _adjust(self, new_min_key, new_max_key):
        """Adjust the bins, the offset, the min_key, and max_key, without resizing the
        bins, in order to try making it fit the specified range.
        """
        self._center_bins(new_min_key, new_max_key)
        self.min_key = new_min_key
        self.max_key = new_max_key

    def _shift_bins(self, shift):
        """shift the bins; this changes the offset"""
        if shift > 0:
            self.bins = self.bins[:-shift]
            self.bins[:0] = [0] * shift
        else:
            self.bins = self.bins[abs(shift) :]
            self.bins.extend([0] * abs(shift))
        self.offset -= shift

    def _center_bins(self, new_min_key, new_max_key):
        """center the bins; this changes the offset"""
        middle_key = new_min_key + (new_max_key - new_min_key + 1) // 2
        self._shift_bins(self.offset + self.length() // 2 - middle_key)

    def key_at_rank(self, rank, lower=True):
        running_ct = 0
        for i, bin_ct in enumerate(self.bins):
            running_ct += bin_ct
            if (lower and running_ct > rank) or (not lower and running_ct >= rank + 1):
                return i + self.offset

        return self.max_key

    def merge(self, store):
        if store.count == 0:
            return

        if self.count == 0:
            self.copy(store)
            return

        if store.min_key < self.min_key or store.max_key > self.max_key:
            self._extend_range(store.min_key, store.max_key)

        for key in range(store.min_key, store.max_key + 1):
            self.bins[key - self.offset] += store.bins[key - store.offset]

        self.count += store.count


class CollapsingLowestDenseStore(DenseStore):
    """A dense store that keeps all the bins between the bin for the min_key and the
    bin for the max_key, but collapsing the left-most bins if the number of bins
    exceeds the bin_limit

    Args:
        bin_limit (int): the maximum number of bins
        chunk_size (int, optional): the number of bins to grow by

    Attributes:
        count (int): the sum of the counts for the bins
        min_key (int): the minimum key bin
        max_key (int): the maximum key bin
        offset (int): the difference btw the keys and the index in which they are stored
        bins (List[int]): the bins
    """

    def __init__(self, bin_limit, chunk_size=CHUNK_SIZE):
        super().__init__()
        self.bin_limit = bin_limit
        self.is_collapsed = False

    def copy(self, store):
        self.bin_limit = store.bin_limit
        self.is_collapsed = store.is_collapsed
        super().copy(store)

    def _get_new_length(self, new_min_key, new_max_key):
        desired_length = new_max_key - new_min_key + 1
        return min(
            self.chunk_size * math.ceil((desired_length) / self.chunk_size),
            self.bin_limit,
        )

    def _get_index(self, key):
        """calculate the bin index for the key, extending the range if necessary"""
        if key < self.min_key:
            if self.is_collapsed:
                return 0

            self._extend_range(key)
            if self.is_collapsed:
                return 0
        elif key > self.max_key:
            self._extend_range(key)

        return key - self.offset

    def _adjust(self, new_min_key, new_max_key):
        """Override. Adjust the bins, the offset, the min_key, and max_key, without
        resizing the bins, in order to try making it fit the specified
        range. Collapse to the left if necessary.
        """

        if new_max_key - new_min_key + 1 > self.length():
            # The range of keys is too wide, the lowest bins need to be collapsed.
            new_min_key = new_max_key - self.length() + 1

            if new_min_key >= self.max_key:
                # put everything in the first bin
                self.offset = new_min_key
                self.min_key = new_min_key
                self.bins[:] = [0] * self.length()
                self.bins[0] = self.count
            else:
                shift = self.offset - new_min_key
                if shift < 0:
                    collapse_start_index = self.min_key - self.offset
                    collapse_end_index = new_min_key - self.offset
                    collapsed_count = sum(
                        self.bins[collapse_start_index:collapse_end_index]
                    )
                    self.bins[collapse_start_index:collapse_end_index] = [0] * (
                        new_min_key - self.min_key
                    )
                    self.bins[collapse_end_index] += collapsed_count
                    self.min_key = new_min_key
                    # shift the buckets to make room for new_max_key
                    self._shift_bins(shift)
                else:
                    self.min_key = new_min_key
                    # shift the buckets to make room for new_min_key
                    self._shift_bins(shift)

            self.max_key = new_max_key
            self.is_collapsed = True
        else:
            self._center_bins(new_min_key, new_max_key)
            self.min_key = new_min_key
            self.max_key = new_max_key

    def merge(self, store):
        """Override."""
        if store.count == 0:
            return

        if self.count == 0:
            self.copy(store)
            return

        if store.min_key < self.min_key or store.max_key > self.max_key:
            self._extend_range(store.min_key, store.max_key)

        collapse_start_idx = store.min_key - store.offset
        collapse_end_idx = min(self.min_key, store.max_key + 1) - store.offset
        if collapse_end_idx > collapse_start_idx:
            collapse_count = sum(store.bins[collapse_start_idx:collapse_end_idx])
            self.bins[0] += collapse_count
        else:
            collapse_end_idx = collapse_start_idx

        for key in range(collapse_end_idx + store.offset, store.max_key + 1):
            self.bins[key - self.offset] += store.bins[key - store.offset]

        self.count += store.count


class CollapsingHighestDenseStore(DenseStore):
    """A dense store that keeps all the bins between the bin for the min_key and the
    bin for the max_key, but collapsing the right-most bins if the number of bins
    exceeds the bin_limit

    Args:
        bin_limit (int): the maximum number of bins
        chunk_size (int, optional): the number of bins to grow by

    Attributes:
        count (int): the sum of the counts for the bins
        min_key (int): the minimum key bin
        max_key (int): the maximum key bin
        offset (int): the difference btw the keys and the index in which they are stored
        bins (List[int]): the bins
    """

    def __init__(self, bin_limit, chunk_size=CHUNK_SIZE):
        super().__init__()
        self.bin_limit = bin_limit
        self.is_collapsed = False

    def copy(self, store):
        self.bin_limit = store.bin_limit
        self.is_collapsed = store.is_collapsed
        super().copy(store)

    def _get_new_length(self, new_min_key, new_max_key):
        desired_length = new_max_key - new_min_key + 1
        return min(
            self.chunk_size * math.ceil((desired_length) / self.chunk_size),
            self.bin_limit,
        )

    def _get_index(self, key):
        """calculate the bin index for the key, extending the range if necessary"""
        if key > self.max_key:
            if self.is_collapsed:
                return self.length() - 1

            self._extend_range(key)
            if self.is_collapsed:
                return self.length() - 1
        elif key < self.min_key:
            self._extend_range(key)
        return key - self.offset

    def _adjust(self, new_min_key, new_max_key):
        """Override. Adjust the bins, the offset, the min_key, and max_key, without
        resizing the bins, in order to try making it fit the specified
        range. Collapse to the left if necessary.
        """

        if new_max_key - new_min_key + 1 > self.length():
            # The range of keys is too wide, the lowest bins need to be collapsed.
            new_max_key = new_min_key + self.length() - 1

            if new_max_key <= self.min_key:
                # put everything in the last bin
                self.offset = new_min_key
                self.max_key = new_max_key
                self.bins[:] = [0] * self.length()
                self.bins[-1] = self.count
            else:
                shift = self.offset - new_min_key
                if shift > 0:
                    collapse_start_index = new_max_key - self.offset + 1
                    collapse_end_index = self.max_key - self.offset + 1
                    collapsed_count = sum(
                        self.bins[collapse_start_index:collapse_end_index]
                    )
                    self.bins[collapse_start_index:collapse_end_index] = [0] * (
                        self.max_key - new_max_key
                    )
                    self.bins[collapse_start_index - 1] += collapsed_count
                    self.max_key = new_max_key
                    # shift the buckets to make room for new_max_key
                    self._shift_bins(shift)
                else:
                    self.max_key = new_max_key
                    # shift the buckets to make room for new_min_key
                    self._shift_bins(shift)

            self.min_key = new_min_key
            self.is_collapsed = True
        else:
            self._center_bins(new_min_key, new_max_key)
            self.min_key = new_min_key
            self.max_key = new_max_key

    def merge(self, store):
        """Override."""
        if store.count == 0:
            return

        if self.count == 0:
            self.copy(store)
            return

        if store.min_key < self.min_key or store.max_key > self.max_key:
            self._extend_range(store.min_key, store.max_key)

        collapse_end_idx = store.max_key - store.offset + 1
        collapse_start_idx = max(self.max_key + 1, store.min_key) - store.offset
        if collapse_end_idx > collapse_start_idx:
            collapse_count = sum(store.bins[collapse_start_idx:collapse_end_idx])
            self.bins[-1] += collapse_count
        else:
            collapse_start_idx = collapse_end_idx

        for key in range(store.min_key, collapse_start_idx + store.offset):
            self.bins[key - self.offset] += store.bins[key - store.offset]

        self.count += store.count
