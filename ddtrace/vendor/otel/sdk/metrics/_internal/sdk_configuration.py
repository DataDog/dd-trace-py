# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# pylint: disable=unused-import

from collections.abc import Sequence
from dataclasses import dataclass

# This kind of import is needed to avoid Sphinx errors.
import ddtrace.vendor.otel.sdk.metrics
import ddtrace.vendor.otel.sdk.resources


@dataclass
class SdkConfiguration:
    exemplar_filter: "ddtrace.vendor.otel.sdk.metrics.ExemplarFilter"
    resource: "ddtrace.vendor.otel.sdk.resources.Resource"
    views: Sequence["ddtrace.vendor.otel.sdk.metrics.view.View"]
