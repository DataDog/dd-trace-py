# Unless explicitly stated otherwise all files in this repository are licensed
# under the Apache License 2.0.
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2020 Datadog, Inc.

"""Exceptions thrown by DDSketch"""


class IllegalArgumentException(Exception):
    """thrown when an argument is misspecified"""


class UnequalSketchParametersException(Exception):
    """thrown when trying to merge two sketches with different relative_accuracy
    parameters
    """
