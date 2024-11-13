from copy import deepcopy
import itertools
import sys
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Union
from typing import cast

from ddtrace._trace._span_pointer import _SpanPointerDescription
from ddtrace._trace._span_pointer import _SpanPointerDirection
from ddtrace._trace._span_pointer import _standard_hashing_function
from ddtrace._trace.utils_botocore.span_pointers.telemetry import record_span_pointer_calculation_issue
from ddtrace.internal.logger import get_logger


if sys.version_info >= (3, 8):
    from typing import TypedDict
else:
    from typing_extensions import TypedDict

log = get_logger(__name__)


_DynamoDBTableName = str
_DynamoDBItemFieldName = str
_DynamoDBItemTypeTag = str

_DynamoDBItemValue = Dict[_DynamoDBItemTypeTag, Any]
_DynamoDBItem = Dict[_DynamoDBItemFieldName, _DynamoDBItemValue]

_DynamoDBItemPrimaryKeyValue = Dict[_DynamoDBItemTypeTag, str]  # must be length 1
_DynamoDBItemPrimaryKey = Dict[_DynamoDBItemFieldName, _DynamoDBItemPrimaryKeyValue]


class _DynamoDBPutRequest(TypedDict):
    Item: _DynamoDBItem


class _DynamoDBPutRequestWriteRequest(TypedDict):
    PutRequest: _DynamoDBPutRequest


class _DynamoDBDeleteRequest(TypedDict):
    Key: _DynamoDBItemPrimaryKey


class _DynamoDBDeleteRequestWriteRequest(TypedDict):
    DeleteRequest: _DynamoDBDeleteRequest


_DynamoDBWriteRequest = Union[_DynamoDBPutRequestWriteRequest, _DynamoDBDeleteRequestWriteRequest]


class _DynamoDBTransactConditionCheck(TypedDict, total=False):
    # https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_ConditionCheck.html
    Key: _DynamoDBItemPrimaryKey
    TableName: _DynamoDBTableName


class _DynamoDBTransactConditionCheckItem(TypedDict):
    ConditionCheck: _DynamoDBTransactConditionCheck


class _DynanmoDBTransactDelete(TypedDict, total=False):
    # https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_Delete.html
    Key: _DynamoDBItemPrimaryKey
    TableName: _DynamoDBTableName


class _DynamoDBTransactDeleteItem(TypedDict):
    Delete: _DynanmoDBTransactDelete


class _DynamoDBTransactPut(TypedDict, total=False):
    # https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_Put.html
    Item: _DynamoDBItem
    TableName: _DynamoDBTableName


class _DynamoDBTransactPutItem(TypedDict):
    Put: _DynamoDBTransactPut


class _DynamoDBTransactUpdate(TypedDict, total=False):
    # https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_Update.html
    Key: _DynamoDBItemPrimaryKey
    TableName: _DynamoDBTableName


class _DynamoDBTransactUpdateItem(TypedDict):
    Update: _DynamoDBTransactUpdate


# https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_TransactWriteItem.html
_DynamoDBTransactWriteItem = Union[
    _DynamoDBTransactConditionCheckItem,
    _DynamoDBTransactDeleteItem,
    _DynamoDBTransactPutItem,
    _DynamoDBTransactUpdateItem,
]


def _extract_span_pointers_for_dynamodb_response(
    dynamodb_primary_key_names_for_tables: Dict[_DynamoDBTableName, Set[_DynamoDBItemFieldName]],
    operation_name: str,
    request_parameters: Dict[str, Any],
    response: Dict[str, Any],
) -> List[_SpanPointerDescription]:
    if operation_name == "PutItem":
        return _extract_span_pointers_for_dynamodb_putitem_response(
            dynamodb_primary_key_names_for_tables, request_parameters
        )

    elif operation_name in ("UpdateItem", "DeleteItem"):
        return _extract_span_pointers_for_dynamodb_keyed_operation_response(
            operation_name,
            request_parameters,
        )

    elif operation_name == "BatchWriteItem":
        return _extract_span_pointers_for_dynamodb_batchwriteitem_response(
            dynamodb_primary_key_names_for_tables,
            request_parameters,
            response,
        )

    elif operation_name == "TransactWriteItems":
        return _extract_span_pointers_for_dynamodb_transactwriteitems_response(
            dynamodb_primary_key_names_for_tables,
            request_parameters,
        )

    else:
        return []


def _extract_span_pointers_for_dynamodb_putitem_response(
    dynamodb_primary_key_names_for_tables: Dict[_DynamoDBTableName, Set[_DynamoDBItemFieldName]],
    request_parameters: Dict[str, Any],
) -> List[_SpanPointerDescription]:
    try:
        table_name = request_parameters["TableName"]
        item = request_parameters["Item"]
    except KeyError as e:
        log.debug(
            "failed to extract DynamoDB.PutItem span pointer: missing key %s",
            e,
        )
        record_span_pointer_calculation_issue(operation="DynamoDB.PutItem", issue_tag="request_parameters")
        return []

    try:
        primary_key_names = dynamodb_primary_key_names_for_tables[table_name]
    except KeyError as e:
        log.warning(
            "failed to extract DynamoDB.PutItem span pointer: table %s not found in primary key names",
            e,
        )
        record_span_pointer_calculation_issue(operation="DynamoDB.PutItem", issue_tag="missing_table_info")
        return []

    try:
        primary_key = _aws_dynamodb_item_primary_key_from_item(
            operation="DynamoDB.PutItem",
            primary_key_field_names=primary_key_names,
            item=item,
        )
        if primary_key is None:
            return []

        span_pointer_description = _aws_dynamodb_item_span_pointer_description(
            operation="DynamoDB.PutItem",
            pointer_direction=_SpanPointerDirection.DOWNSTREAM,
            table_name=table_name,
            primary_key=primary_key,
        )
        if span_pointer_description is None:
            return []

        return [span_pointer_description]

    except Exception as e:
        log.debug(
            "failed to generate DynamoDB.PutItem span pointer: %s",
            str(e),
        )
        record_span_pointer_calculation_issue(operation="DynamoDB.PutItem", issue_tag="calculation")
        return []


def _extract_span_pointers_for_dynamodb_keyed_operation_response(
    operation_name: str,
    request_parmeters: Dict[str, Any],
) -> List[_SpanPointerDescription]:
    try:
        table_name = request_parmeters["TableName"]
        key = request_parmeters["Key"]
    except KeyError as e:
        log.debug(
            "failed to extract DynamoDB.%s span pointer: missing key %s",
            operation_name,
            e,
        )
        record_span_pointer_calculation_issue(operation=f"DynamoDB.{operation_name}", issue_tag="request_parameters")
        return []

    try:
        span_pointer_description = _aws_dynamodb_item_span_pointer_description(
            operation=f"DynamoDB.{operation_name}",
            pointer_direction=_SpanPointerDirection.DOWNSTREAM,
            table_name=table_name,
            primary_key=key,
        )
        if span_pointer_description is None:
            return []

        return [span_pointer_description]

    except Exception as e:
        log.debug(
            "failed to generate DynamoDB.%s span pointer: %s",
            operation_name,
            str(e),
        )
        record_span_pointer_calculation_issue(operation=f"DynamoDB.{operation_name}", issue_tag="calculation")
        return []


def _extract_span_pointers_for_dynamodb_batchwriteitem_response(
    dynamodb_primary_key_names_for_tables: Dict[_DynamoDBTableName, Set[_DynamoDBItemFieldName]],
    request_parameters: Dict[str, Any],
    response: Dict[str, Any],
) -> List[_SpanPointerDescription]:
    try:
        requested_items = request_parameters["RequestItems"]
        unprocessed_items = response.get("UnprocessedItems", {})

        processed_items = _identify_dynamodb_batch_write_item_processed_items(requested_items, unprocessed_items)
        if processed_items is None:
            return []

    except Exception as e:
        log.debug(
            "failed to extract DynamoDB.BatchWriteItem span pointers: %s",
            str(e),
        )
        record_span_pointer_calculation_issue(operation="DynamoDB.BatchWriteItem", issue_tag="request_parameters")
        return []

    try:
        result = []
        for table_name, processed_items_for_table in processed_items.items():
            for write_request in processed_items_for_table:
                primary_key = _aws_dynamodb_item_primary_key_from_write_request(
                    dynamodb_primary_key_names_for_tables=dynamodb_primary_key_names_for_tables,
                    table_name=table_name,
                    write_request=write_request,
                )
                if primary_key is None:
                    # TODO: should this be a continue instead?
                    return []

                span_pointer_description = _aws_dynamodb_item_span_pointer_description(
                    operation="DynamoDB.BatchWriteItem",
                    pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                    table_name=table_name,
                    primary_key=primary_key,
                )
                if span_pointer_description is None:
                    # TODO: should this be a continue instead?
                    return []

                result.append(span_pointer_description)

        return result

    except Exception as e:
        log.debug(
            "failed to generate DynamoDB.BatchWriteItem span pointer: %s",
            str(e),
        )
        record_span_pointer_calculation_issue(operation="DynamoDB.BatchWriteItem", issue_tag="calculation")
        return []


def _extract_span_pointers_for_dynamodb_transactwriteitems_response(
    dynamodb_primary_key_names_for_tables: Dict[_DynamoDBTableName, Set[_DynamoDBItemFieldName]],
    request_parameters: Dict[str, Any],
) -> List[_SpanPointerDescription]:
    try:
        return list(
            itertools.chain.from_iterable(
                _aws_dynamodb_item_span_pointer_description_for_transactwrite_request(
                    dynamodb_primary_key_names_for_tables=dynamodb_primary_key_names_for_tables,
                    transact_write_request=transact_write_request,
                )
                for transact_write_request in request_parameters["TransactItems"]
            )
        )

    except Exception as e:
        log.debug(
            "failed to generate DynamoDB.TransactWriteItems span pointer: %s",
            str(e),
        )
        record_span_pointer_calculation_issue(operation="DynamoDB.TransactWriteItems", issue_tag="calculation")
        return []


def _identify_dynamodb_batch_write_item_processed_items(
    requested_items: Dict[_DynamoDBTableName, List[_DynamoDBWriteRequest]],
    unprocessed_items: Dict[_DynamoDBTableName, List[_DynamoDBWriteRequest]],
) -> Optional[Dict[_DynamoDBTableName, List[_DynamoDBWriteRequest]]]:
    processed_items = {}

    if not all(table_name in requested_items for table_name in unprocessed_items):
        log.debug("DynamoDB.BAtchWriteItem unprocessed items include tables not in the requested items")
        record_span_pointer_calculation_issue(operation="DynamoDB.BatchWriteItem", issue_tag="unprocessed_items")
        return None

    for table_name, requested_write_requests in requested_items.items():
        if table_name not in unprocessed_items:
            processed_items[table_name] = deepcopy(requested_write_requests)

        else:
            if not all(
                unprocessed_write_request in requested_write_requests
                for unprocessed_write_request in unprocessed_items[table_name]
            ):
                log.debug(
                    "DynamoDB.BatchWriteItem unprocessed write requests include items not in the "
                    "requested write requests"
                )
                record_span_pointer_calculation_issue(
                    operation="DynamoDB.BatchWriteItem", issue_tag="unprocessed_items"
                )
                return None

            these_processed_items = [
                deepcopy(processed_write_request)
                for processed_write_request in requested_write_requests
                if processed_write_request not in unprocessed_items[table_name]
            ]
            if these_processed_items:
                # no need to include them if they are all unprocessed
                processed_items[table_name] = these_processed_items

    return processed_items


def _aws_dynamodb_item_primary_key_from_item(
    operation: str,
    primary_key_field_names: Set[_DynamoDBItemFieldName],
    item: _DynamoDBItem,
) -> Optional[_DynamoDBItemPrimaryKey]:
    if len(primary_key_field_names) not in (1, 2):
        log.debug("unexpected number of primary key fields: %d", len(primary_key_field_names))
        record_span_pointer_calculation_issue(operation=operation, issue_tag="primary_key_fields")
        return None

    result = {}
    for primary_key_field_name in primary_key_field_names:
        primary_key_field_value = _aws_dynamodb_extract_and_verify_primary_key_field_value_item(
            operation, item, primary_key_field_name
        )
        if primary_key_field_value is None:
            return None

        result[primary_key_field_name] = primary_key_field_value

    return result


def _aws_dynamodb_item_primary_key_from_write_request(
    dynamodb_primary_key_names_for_tables: Dict[_DynamoDBTableName, Set[_DynamoDBItemFieldName]],
    table_name: _DynamoDBTableName,
    write_request: _DynamoDBWriteRequest,
) -> Optional[_DynamoDBItemPrimaryKey]:
    # https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_WriteRequest.html

    if len(write_request) != 1:
        log.debug("unexpected number of write request fields: %d", len(write_request))
        record_span_pointer_calculation_issue(operation="DynamoDB.BatchWriteItem", issue_tag="write_request_shape")
        return None

    if "PutRequest" in write_request:
        # Unfortunately mypy doesn't properly see the if statement above as a
        # type-narrowing from _DynamoDBWriteRequest to
        # _DynamoDBPutRequestWriteRequest, so we help it out ourselves.
        write_request = cast(_DynamoDBPutRequestWriteRequest, write_request)

        return _aws_dynamodb_item_primary_key_from_item(
            operation="DynamoDB.BatchWriteItem",
            primary_key_field_names=dynamodb_primary_key_names_for_tables[table_name],
            item=write_request["PutRequest"]["Item"],
        )

    elif "DeleteRequest" in write_request:
        # Unfortunately mypy doesn't properly see the if statement above as a
        # type-narrowing from _DynamoDBWriteRequest to
        # _DynamoDBDeleteRequestWriteRequest, so we help it out ourselves.
        write_request = cast(_DynamoDBDeleteRequestWriteRequest, write_request)

        return write_request["DeleteRequest"]["Key"]

    else:
        log.debug("unexpected write request structure: %s", "".join(sorted(write_request.keys())))
        record_span_pointer_calculation_issue(operation="DynamoDB.BatchWriteItem", issue_tag="write_request_shape")
        return None


def _aws_dynamodb_item_span_pointer_description_for_transactwrite_request(
    dynamodb_primary_key_names_for_tables: Dict[_DynamoDBTableName, Set[_DynamoDBItemFieldName]],
    transact_write_request: _DynamoDBTransactWriteItem,
) -> List[_SpanPointerDescription]:
    if len(transact_write_request) != 1:
        log.debug("unexpected number of transact write request fields: %d", len(transact_write_request))
        record_span_pointer_calculation_issue(
            operation="DynamoDB.TransactWriteItems", issue_tag="transactwrite_request_shape"
        )
        return []

    if "ConditionCheck" in transact_write_request:
        # ConditionCheck requests don't actually modify anything, so we don't
        # consider the associated item to be passing information between spans.
        return []

    elif "Delete" in transact_write_request:
        # Unfortunately mypy does not properly see the if statement above as a
        # type-narrowing from _DynamoDBTransactWriteItem to
        # _DynamoDBTransactDeleteItem, so we help it out ourselves.

        transact_write_request = cast(_DynamoDBTransactDeleteItem, transact_write_request)

        table_name = transact_write_request["Delete"]["TableName"]
        key = transact_write_request["Delete"]["Key"]

    elif "Put" in transact_write_request:
        # Unfortunately mypy does not properly see the if statement above as a
        # type-narrowing from _DynamoDBTransactWriteItem to
        # _DynamoDBTransactPutItem, so we help it out ourselves.

        transact_write_request = cast(_DynamoDBTransactPutItem, transact_write_request)

        table_name = transact_write_request["Put"]["TableName"]
        primary_key = _aws_dynamodb_item_primary_key_from_item(
            operation="DynamoDB.TransactWriteItems",
            primary_key_field_names=dynamodb_primary_key_names_for_tables[table_name],
            item=transact_write_request["Put"]["Item"],
        )
        if primary_key is None:
            return []
        key = primary_key

    elif "Update" in transact_write_request:
        # Unfortunately mypy does not properly see the if statement above as a
        # type-narrowing from _DynamoDBTransactWriteItem to
        # _DynamoDBTransactUpdateItem, so we help it out ourselves.

        transact_write_request = cast(_DynamoDBTransactUpdateItem, transact_write_request)

        table_name = transact_write_request["Update"]["TableName"]
        key = transact_write_request["Update"]["Key"]

    else:
        log.debug("unexpected transact write request structure: %s", "".join(sorted(transact_write_request.keys())))
        record_span_pointer_calculation_issue(
            operation="DynamoDB.TransactWriteItems", issue_tag="transactwrite_request_shape"
        )
        return []

    span_pointer_description = _aws_dynamodb_item_span_pointer_description(
        operation="DynamoDB.TransactWriteItems",
        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
        table_name=table_name,
        primary_key=key,
    )
    if span_pointer_description is None:
        return []

    return [span_pointer_description]


def _aws_dynamodb_item_span_pointer_description(
    operation: str,
    pointer_direction: _SpanPointerDirection,
    table_name: _DynamoDBTableName,
    primary_key: _DynamoDBItemPrimaryKey,
) -> Optional[_SpanPointerDescription]:
    pointer_hash = _aws_dynamodb_item_span_pointer_hash(operation, table_name, primary_key)
    if pointer_hash is None:
        return None

    return _SpanPointerDescription(
        pointer_kind="aws.dynamodb.item",
        pointer_direction=pointer_direction,
        pointer_hash=pointer_hash,
        extra_attributes={},
    )


def _aws_dynamodb_extract_and_verify_primary_key_field_value_item(
    operation: str,
    item: _DynamoDBItem,
    primary_key_field_name: _DynamoDBItemFieldName,
) -> Optional[_DynamoDBItemPrimaryKeyValue]:
    if primary_key_field_name not in item:
        log.debug("missing primary key field: %s", primary_key_field_name)
        record_span_pointer_calculation_issue(operation=operation, issue_tag="primary_key_extraction")
        return None

    value_object = item[primary_key_field_name]

    if len(value_object) != 1:
        log.debug("primary key field %s must have exactly one value: %d", primary_key_field_name, len(value_object))
        record_span_pointer_calculation_issue(operation=operation, issue_tag="primary_key_extraction")
        return None

    value_type, value_data = next(iter(value_object.items()))
    if value_type not in ("S", "N", "B"):
        log.debug("unexpected primary key field %s value type: %s", primary_key_field_name, value_type)
        record_span_pointer_calculation_issue(operation=operation, issue_tag="primary_key_extraction")
        return None

    if not isinstance(value_data, str):
        log.debug("unexpected primary key field %s value data type: %s", primary_key_field_name, type(value_data))
        record_span_pointer_calculation_issue(operation=operation, issue_tag="primary_key_extraction")
        return None

    return {value_type: value_data}


def _aws_dynamodb_item_span_pointer_hash(
    operation: str, table_name: _DynamoDBTableName, primary_key: _DynamoDBItemPrimaryKey
) -> Optional[str]:
    if len(primary_key) == 1:
        key, value_object = next(iter(primary_key.items()))
        encoded_key_1 = key.encode("utf-8")

        encoded_value_1 = _aws_dynamodb_item_encode_primary_key_value(value_object)
        if encoded_value_1 is None:
            return None

        encoded_key_2 = b""
        encoded_value_2 = b""

    elif len(primary_key) == 2:
        (key_1, value_object_1), (key_2, value_object_2) = sorted(
            primary_key.items(), key=lambda x: x[0].encode("utf-8")
        )
        encoded_key_1 = key_1.encode("utf-8")

        encoded_value_1 = _aws_dynamodb_item_encode_primary_key_value(value_object_1)
        if encoded_value_1 is None:
            return None

        encoded_key_2 = key_2.encode("utf-8")

        maybe_encoded_value_2 = _aws_dynamodb_item_encode_primary_key_value(value_object_2)
        if maybe_encoded_value_2 is None:
            return None
        encoded_value_2 = maybe_encoded_value_2

    else:
        log.debug("unexpected number of primary key fields: %d", len(primary_key))
        record_span_pointer_calculation_issue(operation=operation, issue_tag="primary_key_extraction")
        return None

    return _standard_hashing_function(
        table_name.encode("utf-8"),
        encoded_key_1,
        encoded_value_1,
        encoded_key_2,
        encoded_value_2,
    )


def _aws_dynamodb_item_encode_primary_key_value(value_object: _DynamoDBItemPrimaryKeyValue) -> Optional[bytes]:
    if len(value_object) != 1:
        log.debug("primary key value object must have exactly one field: %d", len(value_object))
        record_span_pointer_calculation_issue(operation="DynamoDB", issue_tag="primary_key_encoding")
        return None

    value_type, value = next(iter(value_object.items()))

    if value_type == "S":
        return value.encode("utf-8")

    if value_type in ("N", "B"):
        # these should already be here as ASCII strings
        return value.encode("ascii")

    log.debug("unexpected primary key value type: %s", value_type)
    record_span_pointer_calculation_issue(operation="DynamoDB", issue_tag="primary_key_encoding")
    return None
