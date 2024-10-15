from typing import Any
from typing import Dict
from typing import List
from typing import Set

from ddtrace._trace._span_pointer import _SpanPointerDescription
from ddtrace._trace._span_pointer import _SpanPointerDirection
from ddtrace._trace._span_pointer import _standard_hashing_function
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


_DynamoDBTableName = str
_DynamoDBItemFieldName = str
_DynamoDBItemTypeTag = str

_DynamoDBItemValue = Dict[_DynamoDBItemTypeTag, Any]
_DynamoDBItem = Dict[_DynamoDBItemFieldName, _DynamoDBItemValue]

_DynamoDBItemPrimaryKeyValue = Dict[_DynamoDBItemTypeTag, str]  # must be length 1
_DynamoDBItemPrimaryKey = Dict[_DynamoDBItemFieldName, _DynamoDBItemPrimaryKeyValue]


def _extract_span_pointers_for_dynamodb_response(
    dynamodb_primary_key_names_for_tables: Dict[_DynamoDBTableName, Set[_DynamoDBItemFieldName]],
    operation_name: str,
    request_parameters: Dict[str, Any],
) -> List[_SpanPointerDescription]:
    if operation_name == "PutItem":
        return _extract_span_pointers_for_dynamodb_putitem_response(
            dynamodb_primary_key_names_for_tables, request_parameters
        )

    if operation_name in ("UpdateItem", "DeleteItem"):
        return _extract_span_pointers_for_dynamodb_keyed_operation_response(
            operation_name,
            request_parameters,
        )

    return []


def _extract_span_pointers_for_dynamodb_putitem_response(
    dynamodb_primary_key_names_for_tables: Dict[_DynamoDBTableName, Set[_DynamoDBItemFieldName]],
    request_parameters: Dict[str, Any],
) -> List[_SpanPointerDescription]:
    try:
        table_name = request_parameters["TableName"]
        item = request_parameters["Item"]

        return [
            _aws_dynamodb_item_span_pointer_description(
                pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                table_name=table_name,
                primary_key=_aws_dynamodb_item_primary_key_from_item(
                    dynamodb_primary_key_names_for_tables[table_name], item
                ),
            )
        ]

    except Exception as e:
        log.warning(
            "failed to generate DynamoDB.PutItem span pointer: %s",
            str(e),
        )
        return []


def _extract_span_pointers_for_dynamodb_keyed_operation_response(
    operation_name: str,
    request_parmeters: Dict[str, Any],
) -> List[_SpanPointerDescription]:
    try:
        table_name = request_parmeters["TableName"]
        key = request_parmeters["Key"]

        return [
            _aws_dynamodb_item_span_pointer_description(
                pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                table_name=table_name,
                primary_key=key,
            )
        ]

    except Exception as e:
        log.warning(
            "failed to generate DynamoDB.%s span pointer: %s",
            operation_name,
            str(e),
        )
        return []


def _aws_dynamodb_item_primary_key_from_item(
    primary_key_field_names: Set[_DynamoDBItemFieldName],
    item: _DynamoDBItem,
) -> _DynamoDBItemPrimaryKey:
    if len(primary_key_field_names) not in (1, 2):
        raise ValueError(f"unexpected number of primary key fields: {len(primary_key_field_names)}")

    return {
        primary_key_field_name: _aws_dynamodb_extract_and_verify_primary_key_field_value_item(
            item, primary_key_field_name
        )
        for primary_key_field_name in primary_key_field_names
    }


def _aws_dynamodb_item_span_pointer_description(
    pointer_direction: _SpanPointerDirection,
    table_name: _DynamoDBTableName,
    primary_key: _DynamoDBItemPrimaryKey,
) -> _SpanPointerDescription:
    return _SpanPointerDescription(
        pointer_kind="aws.dynamodb.item",
        pointer_direction=pointer_direction,
        pointer_hash=_aws_dynamodb_item_span_pointer_hash(table_name, primary_key),
        extra_attributes={},
    )


def _aws_dynamodb_extract_and_verify_primary_key_field_value_item(
    item: _DynamoDBItem,
    primary_key_field_name: _DynamoDBItemFieldName,
) -> _DynamoDBItemPrimaryKeyValue:
    if primary_key_field_name not in item:
        raise ValueError(f"missing primary key field: {primary_key_field_name}")

    value_object = item[primary_key_field_name]

    if len(value_object) != 1:
        raise ValueError(f"primary key field {primary_key_field_name} must have exactly one value: {len(value_object)}")

    value_type, value_data = next(iter(value_object.items()))
    if value_type not in ("S", "N", "B"):
        raise ValueError(f"unexpected primary key field {primary_key_field_name} value type: {value_type}")

    if not isinstance(value_data, str):
        raise ValueError(f"unexpected primary key field {primary_key_field_name} value data type: {type(value_data)}")

    return {value_type: value_data}


def _aws_dynamodb_item_span_pointer_hash(table_name: _DynamoDBTableName, primary_key: _DynamoDBItemPrimaryKey) -> str:
    if len(primary_key) == 1:
        key, value_object = next(iter(primary_key.items()))
        encoded_key_1 = key.encode("utf-8")
        encoded_value_1 = _aws_dynamodb_item_encode_primary_key_value(value_object)
        encoded_key_2 = b""
        encoded_value_2 = b""

    elif len(primary_key) == 2:
        (key_1, value_object_1), (key_2, value_object_2) = sorted(
            primary_key.items(), key=lambda x: x[0].encode("utf-8")
        )
        encoded_key_1 = key_1.encode("utf-8")
        encoded_value_1 = _aws_dynamodb_item_encode_primary_key_value(value_object_1)
        encoded_key_2 = key_2.encode("utf-8")
        encoded_value_2 = _aws_dynamodb_item_encode_primary_key_value(value_object_2)

    else:
        raise ValueError(f"unexpected number of primary key fields: {len(primary_key)}")

    return _standard_hashing_function(
        table_name.encode("utf-8"),
        encoded_key_1,
        encoded_value_1,
        encoded_key_2,
        encoded_value_2,
    )


def _aws_dynamodb_item_encode_primary_key_value(value_object: _DynamoDBItemPrimaryKeyValue) -> bytes:
    if len(value_object) != 1:
        raise ValueError(f"primary key value object must have exactly one field: {len(value_object)}")

    value_type, value = next(iter(value_object.items()))

    if value_type == "S":
        return value.encode("utf-8")

    if value_type in ("N", "B"):
        # these should already be here as ASCII strings
        return value.encode("ascii")

    raise ValueError(f"unknown primary key value type: {value_type}")
