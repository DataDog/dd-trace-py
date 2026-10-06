# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


from ddtrace.vendor.otel.exporter.otlp.proto.common._internal.metrics_encoder import (
    encode_metrics,
)

__all__ = ["encode_metrics"]
