# Copyright The OpenTelemetry Authors
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

import abc
import concurrent.futures
import logging
import os
import platform
import socket
import sys
import typing
from json import dumps
from os import environ
from types import ModuleType
from typing import List, Optional, cast
from urllib import parse

from opentelemetry.attributes import BoundedAttributes
from opentelemetry.util.types import AttributeValue

# psutil: Optional[ModuleType] = None

# try:
#     import psutil as psutil_module

#     psutil = psutil_module
# except ImportError:
#     pass

LabelValue = AttributeValue
Attributes = typing.Mapping[str, LabelValue]
logger = logging.getLogger(__name__)

DEPLOYMENT_ENVIRONMENT = "deployment.environment"
PROCESS_EXECUTABLE_NAME = "process.executable.name"
SERVICE_NAME = "service.name"
SERVICE_VERSION = "service.version"


class Resource:
    """A Resource is an immutable representation of the entity producing telemetry as Attributes."""

    _attributes: BoundedAttributes
    _schema_url: str

    def __init__(
        self, attributes: Attributes, schema_url: typing.Optional[str] = None
    ):
        self._attributes = BoundedAttributes(attributes=attributes)
        if schema_url is None:
            schema_url = ""
        self._schema_url = schema_url

    @staticmethod
    def create(
        attributes: typing.Optional[Attributes] = None,
        schema_url: typing.Optional[str] = None,
    ) -> "Resource":
        """Creates a new `Resource` from attributes.

        `ResourceDetector` instances should not call this method.

        Args:
            attributes: Optional zero or more key-value pairs.
            schema_url: Optional URL pointing to the schema

        Returns:
            The newly-created Resource.
        """

        if not attributes:
            attributes = {}

        resource = _DEFAULT_RESOURCE.merge(Resource(attributes, schema_url))

        if not resource.attributes.get(SERVICE_NAME, None):
            default_service_name = "unknown_service"
            process_executable_name = cast(
                Optional[str],
                resource.attributes.get(PROCESS_EXECUTABLE_NAME, None),
            )
            if process_executable_name:
                default_service_name += ":" + process_executable_name
            resource = resource.merge(
                Resource({SERVICE_NAME: default_service_name}, schema_url)
            )
        return resource

    @staticmethod
    def get_empty() -> "Resource":
        return _EMPTY_RESOURCE

    @property
    def attributes(self) -> Attributes:
        return self._attributes

    @property
    def schema_url(self) -> str:
        return self._schema_url

    def merge(self, other: "Resource") -> "Resource":
        """Merges this resource and an updating resource into a new `Resource`.

        If a key exists on both the old and updating resource, the value of the
        updating resource will override the old resource value.

        The updating resource's `schema_url` will be used only if the old
        `schema_url` is empty. Attempting to merge two resources with
        different, non-empty values for `schema_url` will result in an error
        and return the old resource.

        Args:
            other: The other resource to be merged.

        Returns:
            The newly-created Resource.
        """
        merged_attributes = dict(self.attributes).copy()
        merged_attributes.update(other.attributes)

        if self.schema_url == "":
            schema_url = other.schema_url
        elif other.schema_url == "":
            schema_url = self.schema_url
        elif self.schema_url == other.schema_url:
            schema_url = other.schema_url
        else:
            logger.error(
                "Failed to merge resources: The two schemas %s and %s are incompatible",
                self.schema_url,
                other.schema_url,
            )
            return self
        return Resource(merged_attributes, schema_url)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Resource):
            return False
        return (
            self._attributes == other._attributes
            and self._schema_url == other._schema_url
        )

    def __hash__(self) -> int:
        return hash(
            f"{dumps(self._attributes.copy(), sort_keys=True)}|{self._schema_url}"
        )

    def to_json(self, indent: Optional[int] = 4) -> str:
        return dumps(
            {
                "attributes": dict(self.attributes),
                "schema_url": self._schema_url,
            },
            indent=indent,
        )


_EMPTY_RESOURCE = Resource({})
_DEFAULT_RESOURCE = Resource(
    {
        "telemetry.sdk.language": "python",
        "telemetry.sdk.name": "opentelemetry",
        "telemetry.sdk.version": "1.0.0",
    }
)


class ResourceDetector():
    def __init__(self, raise_on_error: bool = False) -> None:
        self.raise_on_error = raise_on_error

    @abc.abstractmethod
    def detect(self) -> "Resource":
        """Don't call `Resource.create` here to avoid an infinite loop, instead instantiate `Resource` directly"""
        raise NotImplementedError()

