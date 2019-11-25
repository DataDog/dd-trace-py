# Copyright 2019, OpenTelemetry Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from .datadog import DatadogHTTPPropagator

_PROGPAGATOR_FACTORY = DatadogHTTPPropagator


def set_http_propagator_factory(factory):
    """Sets the propagator factory to be used globally"""
    global _PROGPAGATOR_FACTORY
    _PROGPAGATOR_FACTORY = factory


def HTTPPropagator():
    """Returns and instance of the configured propagator"""
    return _PROGPAGATOR_FACTORY()
