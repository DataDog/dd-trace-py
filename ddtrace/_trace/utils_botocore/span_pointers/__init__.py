from typing import Any
from typing import Dict
from typing import List
from typing import Set

from ddtrace._trace._span_pointer import _SpanPointerDescription
from ddtrace._trace.utils_botocore.span_pointers.dynamodb import _DynamoDBItemFieldName
from ddtrace._trace.utils_botocore.span_pointers.dynamodb import _DynamoDBTableName
from ddtrace._trace.utils_botocore.span_pointers.dynamodb import _extract_span_pointers_for_dynamodb_response

# We are importing this function here because it used to live in this module
# and was imported from here in datadog-lambda-python. Once the import is fixed
# in the next release of that library, we should be able to remove this unused
# import from here as well.
from ddtrace._trace.utils_botocore.span_pointers.s3 import _aws_s3_object_span_pointer_description  # noqa: F401
from ddtrace._trace.utils_botocore.span_pointers.s3 import _extract_span_pointers_for_s3_response


def extract_span_pointers_from_successful_botocore_response(
    dynamodb_primary_key_names_for_tables: Dict[_DynamoDBTableName, Set[_DynamoDBItemFieldName]],
    endpoint_name: str,
    operation_name: str,
    request_parameters: Dict[str, Any],
    response: Dict[str, Any],
) -> List[_SpanPointerDescription]:
    if endpoint_name == "s3":
        return _extract_span_pointers_for_s3_response(operation_name, request_parameters, response)

    if endpoint_name == "dynamodb":
        return _extract_span_pointers_for_dynamodb_response(
            dynamodb_primary_key_names_for_tables, operation_name, request_parameters, response
        )

    return []
