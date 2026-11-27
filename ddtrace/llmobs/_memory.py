from collections.abc import Mapping
from collections.abc import Sequence
import hashlib
import math
from typing import Any
from typing import Optional


MEMORY_READ_OPERATIONS = frozenset({"search_memory", "get_memory"})

MEMORY_SCHEMA_VERSION = "1"
MAX_MEMORY_RECORD_IDS = 50

MEMORY_OPERATION_TAG_KEY = "memory_operation"
MEMORY_TYPE_TAG_KEY = "memory_type"
MEMORY_SCOPE_TAG_KEY = "memory_scope"
MEMORY_PROVIDER_TAG_KEY = "memory_provider"
MEMORY_DETECTION_SOURCE_TAG_KEY = "memory_detection_source"
MEMORY_STORE_ID_TAG_KEY = "memory_store_id"

MEMORY_RECORD_COUNT_METRIC_KEY = "memory_record_count"
MEMORY_HIT_METRIC_KEY = "memory_hit"
MEMORY_TOP_SCORE_METRIC_KEY = "memory_top_score"
MEMORY_MEAN_SCORE_METRIC_KEY = "memory_mean_score"
MEMORY_INJECTED_TOKENS_METRIC_KEY = "memory_injected_tokens"
MEMORY_DUPLICATE_COUNT_METRIC_KEY = "memory_duplicate_count"
MEMORY_SUPERSEDED_COUNT_METRIC_KEY = "memory_superseded_count"
MEMORY_PRE_WRITE_BYTES_METRIC_KEY = "memory_pre_write_bytes"
MEMORY_POST_WRITE_BYTES_METRIC_KEY = "memory_post_write_bytes"
MEMORY_SIZE_DELTA_BYTES_METRIC_KEY = "memory_size_delta_bytes"
MEMORY_COMPACTION_RATIO_METRIC_KEY = "memory_compaction_ratio"


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            coerced = float(value)
        except OverflowError:
            return None
        if math.isfinite(coerced):
            return coerced
    return None


def hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def hash_content(text: str) -> str:
    normalized = " ".join(text.split()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return math.ceil(len(text) / 4)


def resolve_memory_span_kind(operation: str, has_parent: bool) -> str:
    # Root spans must use "workflow" to satisfy backend isValidRoot checks.
    if not has_parent:
        return "workflow"
    return "memory"


def build_memory_annotation(
    memory: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, str], dict[str, float], Any, Any]:
    operation = memory.get("operation")
    memory_type = memory.get("memory_type") or memory.get("type")
    scope = memory.get("scope")
    store_id = memory.get("store_id")
    provider = memory.get("provider")
    detection_source = memory.get("detection_source") or "explicit"
    namespace = memory.get("namespace")
    provenance_source = memory.get("provenance_source")
    provenance_span_id = memory.get("provenance_span_id")
    write_outcome = memory.get("write_outcome")

    principal_hash = memory.get("principal_hash")
    if not principal_hash:
        raw_principal = memory.get("principal") or memory.get("user_id")
        if raw_principal:
            principal_hash = hash_identifier(str(raw_principal))

    tenant_hash = memory.get("tenant_hash")
    if not tenant_hash:
        raw_tenant = memory.get("tenant_id") or memory.get("org_id")
        if raw_tenant:
            tenant_hash = hash_identifier(str(raw_tenant))

    records = memory.get("records")
    content = memory.get("content")
    query = memory.get("query")
    explicit_record_ids = memory.get("record_ids")
    explicit_content_hashes = memory.get("content_hashes")

    records_seq: Optional[Sequence[Any]] = (
        records if isinstance(records, Sequence) and not isinstance(records, (str, bytes)) else None
    )
    collected_record_ids: list[str] = []
    all_content_hashes: list[str] = []
    scores: list[float] = []
    total_chars = 0
    doc_records: list[dict[str, Any]] = []

    if records_seq is not None:
        for rec in records_seq:
            if isinstance(rec, str):
                total_chars += len(rec)
                if rec:
                    all_content_hashes.append(hash_content(rec))
                doc_records.append({"text": rec})
            elif isinstance(rec, Mapping):
                rec_id = rec.get("id")
                rec_name = rec.get("name")
                rec_text = rec.get("text") if rec.get("text") is not None else rec.get("content")
                rec_score = _as_float(rec.get("score"))
                rec_hash = rec.get("content_hash")

                if rec_id is not None and str(rec_id):
                    collected_record_ids.append(str(rec_id))
                text_str = rec_text if isinstance(rec_text, str) else (str(rec_text) if rec_text is not None else "")
                total_chars += len(text_str)
                if rec_hash:
                    all_content_hashes.append(str(rec_hash))
                elif text_str:
                    all_content_hashes.append(hash_content(text_str))
                if rec_score is not None:
                    scores.append(rec_score)

                doc: dict[str, Any] = {"text": text_str}
                if rec_id is not None and str(rec_id):
                    doc["id"] = str(rec_id)
                if rec_name is not None and str(rec_name):
                    doc["name"] = str(rec_name)
                if rec_score is not None:
                    doc["score"] = rec_score
                doc_records.append(doc)

    if isinstance(explicit_record_ids, Sequence) and not isinstance(explicit_record_ids, (str, bytes)):
        final_record_ids = [str(rid) for rid in explicit_record_ids if rid is not None and str(rid)][
            :MAX_MEMORY_RECORD_IDS
        ]
    else:
        final_record_ids = collected_record_ids[:MAX_MEMORY_RECORD_IDS]

    if isinstance(explicit_content_hashes, Sequence) and not isinstance(explicit_content_hashes, (str, bytes)):
        all_content_hashes = [str(h) for h in explicit_content_hashes if h is not None and str(h)]
    elif not all_content_hashes and isinstance(content, str) and content:
        all_content_hashes = [hash_content(content)]
    final_content_hashes = all_content_hashes[:MAX_MEMORY_RECORD_IDS]

    metadata_memory: dict[str, Any] = {"schema_version": MEMORY_SCHEMA_VERSION}
    if operation:
        metadata_memory["operation"] = str(operation)
    if memory_type:
        metadata_memory["type"] = str(memory_type)
    if scope:
        metadata_memory["scope"] = str(scope)
    if store_id:
        metadata_memory["store_id"] = str(store_id)
    if provider:
        metadata_memory["provider"] = str(provider)
    if principal_hash:
        metadata_memory["principal_hash"] = str(principal_hash)
    if tenant_hash:
        metadata_memory["tenant_hash"] = str(tenant_hash)
    if namespace:
        metadata_memory["namespace"] = str(namespace)
    if final_record_ids:
        metadata_memory["record_ids"] = final_record_ids
    if final_content_hashes:
        metadata_memory["content_hashes"] = final_content_hashes
    if provenance_source:
        metadata_memory["provenance_source"] = str(provenance_source)
    if provenance_span_id:
        metadata_memory["provenance_span_id"] = str(provenance_span_id)
    if write_outcome:
        metadata_memory["write_outcome"] = str(write_outcome)

    tags: dict[str, str] = {MEMORY_DETECTION_SOURCE_TAG_KEY: str(detection_source)}
    if operation:
        tags[MEMORY_OPERATION_TAG_KEY] = str(operation)
    if memory_type:
        tags[MEMORY_TYPE_TAG_KEY] = str(memory_type)
    if scope:
        tags[MEMORY_SCOPE_TAG_KEY] = str(scope)
    if provider:
        tags[MEMORY_PROVIDER_TAG_KEY] = str(provider)
    if store_id:
        tags[MEMORY_STORE_ID_TAG_KEY] = str(store_id)

    metrics: dict[str, float] = {}
    record_count: Optional[float] = _as_float(memory.get("record_count"))
    if record_count is None:
        if records_seq is not None:
            record_count = float(len(records_seq))
        elif final_record_ids:
            record_count = float(len(final_record_ids))
        elif isinstance(content, str) and content:
            record_count = 1.0

    if record_count is not None:
        metrics[MEMORY_RECORD_COUNT_METRIC_KEY] = record_count

    if operation in MEMORY_READ_OPERATIONS:
        explicit_hit = memory.get("hit")
        if explicit_hit is not None:
            metrics[MEMORY_HIT_METRIC_KEY] = 1.0 if explicit_hit else 0.0
        elif record_count is not None:
            metrics[MEMORY_HIT_METRIC_KEY] = 1.0 if record_count >= 1.0 else 0.0

    if scores:
        metrics[MEMORY_TOP_SCORE_METRIC_KEY] = max(scores)
        mean_score = sum(scores) / len(scores)
        if math.isfinite(mean_score):
            metrics[MEMORY_MEAN_SCORE_METRIC_KEY] = mean_score
    else:
        for src_key, metric_key in (
            ("top_score", MEMORY_TOP_SCORE_METRIC_KEY),
            ("mean_score", MEMORY_MEAN_SCORE_METRIC_KEY),
        ):
            val = _as_float(memory.get(src_key))
            if val is not None:
                metrics[metric_key] = val

    explicit_tokens = _as_float(memory.get("injected_tokens"))
    if explicit_tokens is not None:
        metrics[MEMORY_INJECTED_TOKENS_METRIC_KEY] = explicit_tokens
    elif operation in MEMORY_READ_OPERATIONS:
        if total_chars > 0:
            metrics[MEMORY_INJECTED_TOKENS_METRIC_KEY] = float(math.ceil(total_chars / 4))
        elif isinstance(content, str) and content:
            metrics[MEMORY_INJECTED_TOKENS_METRIC_KEY] = float(estimate_tokens(content))
        elif records_seq is not None:
            metrics[MEMORY_INJECTED_TOKENS_METRIC_KEY] = 0.0

    explicit_dups = _as_float(memory.get("duplicate_count"))
    if explicit_dups is not None:
        metrics[MEMORY_DUPLICATE_COUNT_METRIC_KEY] = explicit_dups
    elif all_content_hashes:
        metrics[MEMORY_DUPLICATE_COUNT_METRIC_KEY] = float(len(all_content_hashes) - len(set(all_content_hashes)))

    superseded = _as_float(memory.get("superseded_count"))
    if superseded is not None:
        metrics[MEMORY_SUPERSEDED_COUNT_METRIC_KEY] = superseded

    pre_bytes = _as_float(memory.get("pre_write_bytes"))
    post_bytes = _as_float(memory.get("post_write_bytes"))
    if pre_bytes is not None:
        metrics[MEMORY_PRE_WRITE_BYTES_METRIC_KEY] = pre_bytes
    if post_bytes is not None:
        metrics[MEMORY_POST_WRITE_BYTES_METRIC_KEY] = post_bytes

    delta_bytes = _as_float(memory.get("size_delta_bytes"))
    if delta_bytes is not None:
        metrics[MEMORY_SIZE_DELTA_BYTES_METRIC_KEY] = delta_bytes
    elif pre_bytes is not None and post_bytes is not None:
        delta = post_bytes - pre_bytes
        if math.isfinite(delta):
            metrics[MEMORY_SIZE_DELTA_BYTES_METRIC_KEY] = delta

    ratio = _as_float(memory.get("compaction_ratio"))
    if ratio is not None:
        metrics[MEMORY_COMPACTION_RATIO_METRIC_KEY] = ratio
    elif operation == "compact_memory" and pre_bytes is not None and post_bytes is not None and pre_bytes > 0:
        computed_ratio = post_bytes / pre_bytes
        if math.isfinite(computed_ratio):
            metrics[MEMORY_COMPACTION_RATIO_METRIC_KEY] = computed_ratio

    normalized_input_data: Any = None
    normalized_output_data: Any = None
    if operation in MEMORY_READ_OPERATIONS:
        if query is not None:
            normalized_input_data = str(query)
        if records_seq is not None and doc_records:
            normalized_output_data = doc_records
    else:
        if query is not None:
            normalized_input_data = str(query)
        elif content is not None:
            normalized_input_data = str(content)
        if records_seq is not None and doc_records:
            normalized_output_data = doc_records

    return metadata_memory, tags, metrics, normalized_input_data, normalized_output_data
