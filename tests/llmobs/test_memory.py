import ast
from dataclasses import dataclass
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import sys
import types
from typing import Any
from typing import Optional
from unittest import mock

import pytest

from ddtrace.contrib.internal.anthropic._constants import TAG_MODEL
from ddtrace.ext import SpanTypes
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs import MemoryAnnotation
from ddtrace.llmobs import MemoryRecord
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._constants import SPAN_START_WHILE_DISABLED_WARNING
from ddtrace.llmobs._integrations.anthropic import AnthropicIntegration
from ddtrace.llmobs._integrations.langgraph import _extract_store_item_record
from ddtrace.llmobs._integrations.langgraph import _parse_store_namespace
from ddtrace.llmobs._llmobs import LLMObsAnnotateSpanError
from ddtrace.llmobs._memory import MAX_MEMORY_RECORD_IDS
from ddtrace.llmobs._memory import build_memory_annotation
from ddtrace.llmobs._memory import estimate_tokens
from ddtrace.llmobs._memory import hash_content
from ddtrace.llmobs._memory import hash_identifier
from ddtrace.llmobs._memory import resolve_memory_span_kind
from ddtrace.llmobs._utils import get_llmobs_input_value
from ddtrace.llmobs._utils import get_llmobs_metadata
from ddtrace.llmobs._utils import get_llmobs_metrics
from ddtrace.llmobs._utils import get_llmobs_output_documents
from ddtrace.llmobs._utils import get_llmobs_output_value
from ddtrace.llmobs._utils import get_llmobs_parent_id
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import get_llmobs_span_name
from ddtrace.llmobs._utils import get_llmobs_tags
from ddtrace.llmobs.decorators import memory


class TestMemoryHelpers:
    def test_hash_identifier_sha256_12_char_hex(self):
        expected = hashlib.sha256(b"user-alice-42").hexdigest()[:12]
        digest = hash_identifier("user-alice-42")
        assert digest == expected
        assert len(digest) == 12
        assert digest == digest.lower()

    def test_hash_content_normalizes_whitespace_and_case(self):
        h1 = hash_content("  User   prefers\nDARK\tmode  ")
        h2 = hash_content("user prefers dark mode")
        assert h1 == h2
        assert h1 == hashlib.sha256(b"user prefers dark mode").hexdigest()[:12]

    def test_estimate_tokens(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("a") == 1
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("abcde") == 2

    def test_resolve_memory_span_kind_root_and_child(self):
        write_and_lifecycle_ops = (
            "create_memory",
            "update_memory",
            "upsert_memory",
            "delete_memory",
            "create_memory_store",
            "delete_memory_store",
        )
        all_ops = ("search_memory", "get_memory", "consolidate_memory", "compact_memory") + write_and_lifecycle_ops
        for op in all_ops:
            assert resolve_memory_span_kind(op, has_parent=False) == "workflow"

        assert resolve_memory_span_kind("search_memory", has_parent=True) == "retrieval"
        assert resolve_memory_span_kind("get_memory", has_parent=True) == "retrieval"
        assert resolve_memory_span_kind("consolidate_memory", has_parent=True) == "task"
        assert resolve_memory_span_kind("compact_memory", has_parent=True) == "task"
        for op in write_and_lifecycle_ops:
            assert resolve_memory_span_kind(op, has_parent=True) == "tool"

    def test_record_ids_and_content_hashes_capped_at_50(self):
        records = [{"id": f"rec-{i}", "text": f"unique memory item {i}"} for i in range(65)]
        meta, _, metrics, _, _ = build_memory_annotation({"operation": "search_memory", "records": records})
        assert len(meta["record_ids"]) == MAX_MEMORY_RECORD_IDS == 50
        assert len(meta["content_hashes"]) == MAX_MEMORY_RECORD_IDS == 50
        assert metrics["memory_record_count"] == 65.0

    def test_typed_dicts_exported(self):
        rec: MemoryRecord = {"id": "r1", "text": "fact", "score": 0.9}
        ann: MemoryAnnotation = {
            "operation": "search_memory",
            "records": [rec],
            "hit": True,
            "top_score": 0.9,
            "mean_score": 0.9,
            "injected_tokens": 12,
            "duplicate_count": 0,
        }
        assert ann["operation"] == "search_memory"

    def test_compaction_ratio_auto_computed_only_for_compact_memory(self):
        _, _, update_metrics, _, _ = build_memory_annotation(
            {"operation": "update_memory", "pre_write_bytes": 400, "post_write_bytes": 100}
        )
        assert update_metrics["memory_size_delta_bytes"] == -300.0
        assert "memory_compaction_ratio" not in update_metrics

        _, _, explicit_metrics, _, _ = build_memory_annotation(
            {
                "operation": "update_memory",
                "pre_write_bytes": 400,
                "post_write_bytes": 100,
                "compaction_ratio": 0.5,
            }
        )
        assert explicit_metrics["memory_compaction_ratio"] == pytest.approx(0.5)

        _, _, compact_metrics, _, _ = build_memory_annotation(
            {"operation": "compact_memory", "pre_write_bytes": 400, "post_write_bytes": 100}
        )
        assert compact_metrics["memory_compaction_ratio"] == pytest.approx(0.25)

    def test_non_finite_and_overflowing_floats_omitted(self):
        meta, _, metrics, _, output = build_memory_annotation(
            {
                "operation": "search_memory",
                "records": [
                    {"id": "r1", "text": "a", "score": float("nan")},
                    {"id": "r2", "text": "b", "score": float("inf")},
                    {"id": "r3", "text": "c", "score": float("-inf")},
                    {"id": "r4", "text": "d", "score": 10**400},
                    {"id": "r5", "text": "e", "score": 0.8},
                ],
                "superseded_count": float("nan"),
                "pre_write_bytes": float("inf"),
                "post_write_bytes": 10**400,
            }
        )
        assert metrics["memory_top_score"] == pytest.approx(0.8)
        assert metrics["memory_mean_score"] == pytest.approx(0.8)
        assert "memory_superseded_count" not in metrics
        assert "memory_pre_write_bytes" not in metrics
        assert "memory_post_write_bytes" not in metrics
        assert [d.get("score") for d in output] == [None, None, None, None, 0.8]
        json.dumps({"meta": meta, "metrics": metrics, "output": output}, allow_nan=False)

        _, _, overflow_metrics, _, _ = build_memory_annotation(
            {
                "operation": "compact_memory",
                "records": [
                    {"id": "x1", "text": "a", "score": 1e308},
                    {"id": "x2", "text": "b", "score": 1e308},
                ],
                "pre_write_bytes": 1e-308,
                "post_write_bytes": 1e308,
            }
        )
        assert overflow_metrics["memory_top_score"] == 1e308
        assert "memory_mean_score" not in overflow_metrics
        assert "memory_compaction_ratio" not in overflow_metrics
        json.dumps(overflow_metrics, allow_nan=False)

        _, _, delta_overflow_metrics, _, _ = build_memory_annotation(
            {"operation": "update_memory", "pre_write_bytes": -1e308, "post_write_bytes": 1e308}
        )
        assert "memory_size_delta_bytes" not in delta_overflow_metrics
        json.dumps(delta_overflow_metrics, allow_nan=False)

        for bad_score in (float("nan"), float("inf"), float("-inf"), 10**400):
            rec = _extract_store_item_record(_MockStoreItem(namespace=("u",), key="k", value={"a": 1}, score=bad_score))
            assert "score" not in rec
        good_rec = _extract_store_item_record(_MockStoreItem(namespace=("u",), key="k", value={"a": 1}, score=0.75))
        assert good_rec["score"] == pytest.approx(0.75)


class TestLLMObsMemoryContextManager:
    def test_standalone_root_span_uses_workflow_kind(self, llmobs, test_spans):
        with llmobs.memory(
            operation="search_memory",
            store_id="mem0-prod",
            memory_type="semantic",
            scope="user",
            provider="mem0",
            principal="alice@example.com",
            tenant_id="org-99",
            namespace="prefs",
        ) as span:
            pass

        assert get_llmobs_parent_id(span) == ROOT_PARENT_ID
        assert get_llmobs_span_kind(span) == "workflow"
        assert get_llmobs_span_name(span) == "search_memory"

        mem_meta = get_llmobs_metadata(span)["memory"]
        assert mem_meta == {
            "schema_version": "1",
            "operation": "search_memory",
            "type": "semantic",
            "scope": "user",
            "store_id": "mem0-prod",
            "provider": "mem0",
            "principal_hash": hash_identifier("alice@example.com"),
            "tenant_hash": hash_identifier("org-99"),
            "namespace": "prefs",
        }
        tags = get_llmobs_tags(span)
        assert tags["memory_operation"] == "search_memory"
        assert tags["memory_type"] == "semantic"
        assert tags["memory_scope"] == "user"
        assert tags["memory_provider"] == "mem0"
        assert tags["memory_detection_source"] == "explicit"
        assert tags["memory_store_id"] == "mem0-prod"
        # Raw PII must never appear in metadata or tags.
        assert "alice@example.com" not in json.dumps(get_llmobs_metadata(span))
        assert "alice@example.com" not in json.dumps(tags)
        assert "org-99" not in json.dumps(get_llmobs_metadata(span))

    def test_root_under_non_llmobs_apm_span_still_uses_workflow_kind(self, llmobs, tracer):
        with tracer.trace("http.request"):
            with llmobs.memory(operation="search_memory", store_id="s1") as span:
                pass
        assert get_llmobs_parent_id(span) == ROOT_PARENT_ID
        assert get_llmobs_span_kind(span) == "workflow"

    def test_child_span_kinds_by_operation(self, llmobs):
        with llmobs.agent("planner"):
            with llmobs.memory(operation="search_memory") as search_span:
                pass
            with llmobs.memory(operation="get_memory") as get_span:
                pass
            with llmobs.memory(operation="create_memory") as create_span:
                pass
            with llmobs.memory(operation="delete_memory_store") as delete_store_span:
                pass
            with llmobs.memory(operation="consolidate_memory") as consolidate_span:
                pass
            with llmobs.memory(operation="compact_memory") as compact_span:
                pass

        assert get_llmobs_span_kind(search_span) == "retrieval"
        assert get_llmobs_span_kind(get_span) == "retrieval"
        assert get_llmobs_span_kind(create_span) == "tool"
        assert get_llmobs_span_kind(delete_store_span) == "tool"
        assert get_llmobs_span_kind(consolidate_span) == "task"
        assert get_llmobs_span_kind(compact_span) == "task"

    def test_errored_memory_span_preserves_prestamped_metadata_and_tags(self, llmobs):
        with pytest.raises(RuntimeError, match="store down"):
            with llmobs.agent("planner"):
                with llmobs.memory(
                    operation="upsert_memory",
                    store_id="redis-mem",
                    scope="session",
                    provider="custom",
                    provenance_source="agent",
                    provenance_span_id="span-123",
                ) as span:
                    raise RuntimeError("store down")

        assert span.error == 1
        assert get_llmobs_span_kind(span) == "tool"
        mem_meta = get_llmobs_metadata(span)["memory"]
        assert mem_meta["operation"] == "upsert_memory"
        assert mem_meta["store_id"] == "redis-mem"
        assert mem_meta["scope"] == "session"
        assert mem_meta["provenance_source"] == "agent"
        assert mem_meta["provenance_span_id"] == "span-123"
        assert mem_meta["schema_version"] == "1"
        tags = get_llmobs_tags(span)
        assert tags["memory_operation"] == "upsert_memory"
        assert tags["memory_scope"] == "session"
        assert tags["memory_store_id"] == "redis-mem"

    def test_disabled_logs_warning(self, llmobs, mock_llmobs_logs):
        llmobs.disable()
        with llmobs.memory(operation="search_memory"):
            pass
        mock_llmobs_logs.warning.assert_called_with(SPAN_START_WHILE_DISABLED_WARNING)


class TestLLMObsAnnotateMemory:
    def test_annotate_read_hit_merges_and_computes_metrics(self, llmobs):
        secret_query = "SSN 123-45-6789 preferences"
        secret_text = "Prefers vegetarian meals"

        with llmobs.agent("assistant"):
            with llmobs.memory(
                operation="search_memory",
                store_id="mem0-main",
                memory_type="episodic",
                scope="user",
                provider="mem0",
                principal="secret-user-id",
            ) as span:
                llmobs.annotate(
                    span=span,
                    metadata={"custom_key": "keep_me"},
                    memory={
                        "query": secret_query,
                        "records": [
                            {"id": "m1", "name": "pref", "text": secret_text, "score": 0.9},
                            {"id": "m2", "name": "pref-dup", "text": "  prefers   VEGETARIAN meals ", "score": 0.7},
                            {"id": "m3", "name": "tz", "text": "UTC+1", "score": 0.5},
                        ],
                    },
                )

        assert get_llmobs_span_kind(span) == "retrieval"
        meta = get_llmobs_metadata(span)
        assert meta["custom_key"] == "keep_me"
        mem_meta = meta["memory"]
        assert mem_meta["operation"] == "search_memory"
        assert mem_meta["store_id"] == "mem0-main"
        assert mem_meta["type"] == "episodic"
        assert mem_meta["scope"] == "user"
        assert mem_meta["provider"] == "mem0"
        assert mem_meta["principal_hash"] == hash_identifier("secret-user-id")
        assert mem_meta["record_ids"] == ["m1", "m2", "m3"]
        assert len(mem_meta["content_hashes"]) == 3
        assert mem_meta["content_hashes"][0] == mem_meta["content_hashes"][1]
        assert mem_meta["schema_version"] == "1"

        metrics = get_llmobs_metrics(span)
        assert all("." not in k for k in metrics)
        assert metrics["memory_record_count"] == 3.0
        assert metrics["memory_hit"] == 1.0
        assert metrics["memory_top_score"] == pytest.approx(0.9)
        assert metrics["memory_mean_score"] == pytest.approx(0.7)
        expected_chars = len(secret_text) + len("  prefers   VEGETARIAN meals ") + len("UTC+1")
        assert metrics["memory_injected_tokens"] == float((expected_chars + 3) // 4)
        assert metrics["memory_duplicate_count"] == 1.0

        # Raw text stays in input/output (covered by SDS), never in metadata or tags.
        assert get_llmobs_input_value(span) == secret_query
        docs = get_llmobs_output_documents(span)
        assert len(docs) == 3
        assert docs[0]["text"] == secret_text
        assert docs[0]["id"] == "m1"
        assert docs[0]["score"] == 0.9

        tags = get_llmobs_tags(span)
        serialized_meta_and_tags = json.dumps({"meta": meta, "tags": tags})
        assert secret_query not in serialized_meta_and_tags
        assert secret_text not in serialized_meta_and_tags
        assert "secret-user-id" not in serialized_meta_and_tags
        assert all("." not in k for k in tags if k.startswith("memory"))

    def test_annotate_read_miss(self, llmobs):
        with llmobs.agent("assistant"):
            with llmobs.memory(operation="search_memory", store_id="s1", scope="user") as span:
                llmobs.annotate(span=span, memory={"query": "unknown topic", "records": []})

        metrics = get_llmobs_metrics(span)
        assert metrics["memory_record_count"] == 0.0
        assert metrics["memory_hit"] == 0.0
        assert metrics["memory_injected_tokens"] == 0.0
        assert get_llmobs_input_value(span) == "unknown topic"
        assert get_llmobs_output_documents(span) is None

    def test_annotate_write_and_compaction_metrics(self, llmobs):
        with llmobs.agent("assistant"):
            with llmobs.memory(operation="compact_memory", store_id="s1", scope="agent") as span:
                llmobs.annotate(
                    span=span,
                    memory={
                        "content": "Consolidated summary of 5 turns",
                        "write_outcome": "update",
                        "superseded_count": 4,
                        "pre_write_bytes": 400,
                        "post_write_bytes": 100,
                    },
                )

        assert get_llmobs_span_kind(span) == "task"
        mem_meta = get_llmobs_metadata(span)["memory"]
        assert mem_meta["write_outcome"] == "update"
        assert mem_meta["content_hashes"] == [hash_content("Consolidated summary of 5 turns")]
        assert "Consolidated summary" not in json.dumps(get_llmobs_metadata(span))
        assert get_llmobs_input_value(span) == "Consolidated summary of 5 turns"

        metrics = get_llmobs_metrics(span)
        assert metrics["memory_superseded_count"] == 4.0
        assert metrics["memory_pre_write_bytes"] == 400.0
        assert metrics["memory_post_write_bytes"] == 100.0
        assert metrics["memory_size_delta_bytes"] == -300.0
        assert metrics["memory_compaction_ratio"] == pytest.approx(0.25)

    def test_annotate_invalid_memory_raises(self, llmobs):
        with llmobs.workflow("wf") as span:
            with pytest.raises(LLMObsAnnotateSpanError):
                llmobs.annotate(span=span, memory="not-a-dict")  # type: ignore[arg-type]


class TestMemoryDecorator:
    def test_sync_decorator_child_and_root(self, llmobs, test_spans):
        @memory(operation="search_memory", store_id="vec-1", scope="user", provider="mem0")
        def recall(query: str):
            LLMObs.annotate(memory={"query": query, "records": [{"id": "r1", "text": "remembered", "score": 0.88}]})
            return ["remembered"]

        with llmobs.agent("bot"):
            assert recall("my query") == ["remembered"]

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        recall_span = [s for s in spans if get_llmobs_span_name(s) == "recall"][0]
        assert get_llmobs_span_kind(recall_span) == "retrieval"
        assert get_llmobs_tags(recall_span)["decorator"] == "1"
        assert get_llmobs_tags(recall_span)["memory_operation"] == "search_memory"
        assert get_llmobs_metrics(recall_span)["memory_hit"] == 1.0
        assert get_llmobs_output_documents(recall_span)[0]["text"] == "remembered"

        # Standalone root call uses "workflow".
        assert recall("root query") == ["remembered"]
        root_spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(root_spans) == 1
        root_span = root_spans[0]
        assert get_llmobs_span_kind(root_span) == "workflow"
        assert get_llmobs_tags(root_span)["memory_operation"] == "search_memory"

    async def test_async_decorator(self, llmobs, test_spans):
        @memory(operation="create_memory", store_id="pg-store", scope="org", provider="custom")
        async def save_fact(fact: str):
            LLMObs.annotate(memory={"content": fact, "write_outcome": "add"})
            return {"saved": True}

        with llmobs.agent("bot"):
            result = await save_fact("company uses Datadog")
        assert result == {"saved": True}

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        save_span = [s for s in spans if get_llmobs_span_name(s) == "save_fact"][0]
        assert get_llmobs_span_kind(save_span) == "tool"
        assert get_llmobs_metadata(save_span)["memory"]["write_outcome"] == "add"
        assert get_llmobs_tags(save_span)["memory_scope"] == "org"
        assert get_llmobs_tags(save_span)["decorator"] == "1"

    def test_sync_and_async_generators(self, llmobs, test_spans):
        @memory(operation="search_memory", store_id="stream-store", scope="session")
        def sync_gen(topic: str):
            yield f"a:{topic}"
            yield f"b:{topic}"

        with llmobs.agent("bot"):
            items = list(sync_gen("docs"))
        assert items == ["a:docs", "b:docs"]
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        gen_span = [s for s in spans if get_llmobs_span_name(s) == "sync_gen"][0]
        assert get_llmobs_span_kind(gen_span) == "retrieval"
        assert get_llmobs_tags(gen_span)["memory_store_id"] == "stream-store"

    async def test_async_generator_decorator(self, llmobs, test_spans):
        @memory(operation="consolidate_memory", store_id="stream-store", scope="agent")
        async def async_gen():
            yield 1
            yield 2

        with llmobs.agent("bot"):
            items = [x async for x in async_gen()]
        assert items == [1, 2]
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        gen_span = [s for s in spans if get_llmobs_span_name(s) == "async_gen"][0]
        assert get_llmobs_span_kind(gen_span) == "task"
        assert get_llmobs_tags(gen_span)["memory_operation"] == "consolidate_memory"

    def test_bare_memory_decorator(self, llmobs, test_spans):
        @memory
        def lookup(query: str) -> str:
            return f"hit:{query}"

        with llmobs.agent("bot"):
            assert lookup("q") == "hit:q"
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        lookup_span = [s for s in spans if get_llmobs_span_name(s) == "lookup"][0]
        assert get_llmobs_span_kind(lookup_span) == "retrieval"
        assert get_llmobs_tags(lookup_span)["memory_operation"] == "search_memory"

    def test_sync_generator_annotate_inputs_error_finishes_span(self, llmobs, tracer, test_spans):
        @memory(operation="search_memory")
        def sync_gen(topic: str):
            yield topic

        with pytest.raises(TypeError):
            list(sync_gen(unexpected_kwarg=1))  # type: ignore[call-arg]

        assert llmobs._instance._current_span() is None
        assert tracer.current_span() is None
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert spans[0].finished
        assert spans[0].error == 1

    async def test_async_generator_annotate_inputs_error_finishes_span(self, llmobs, tracer, test_spans):
        @memory(operation="search_memory")
        async def async_gen(topic: str):
            yield topic

        with pytest.raises(TypeError):
            _ = [x async for x in async_gen(unexpected_kwarg=1)]  # type: ignore[call-arg]

        assert llmobs._instance._current_span() is None
        assert tracer.current_span() is None
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert spans[0].finished
        assert spans[0].error == 1


class TestAnthropicNativeMemoryTool:
    def _run_anthropic_tagging(self, tracer, tools, content_blocks, extra_body=None):
        integration = AnthropicIntegration(integration_config=IntegrationConfig(mock.MagicMock(), "anthropic", {}))
        span = tracer.trace("anthropic.request", span_type=SpanTypes.LLM)
        span.set_tag(TAG_MODEL, "claude-sonnet-4-5")
        response = {
            "role": "assistant",
            "stop_reason": "tool_use",
            "content": content_blocks,
            "usage": {"input_tokens": 100, "output_tokens": 40},
        }
        kwargs: dict[str, Any] = {
            "model": "claude-sonnet-4-5",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Check my notes"}],
        }
        if tools is not None:
            kwargs["tools"] = tools
        if extra_body is not None:
            kwargs["extra_body"] = extra_body
        integration.llmobs_set_tags(span, args=[], kwargs=kwargs, response=response)
        span.finish()
        return span

    def test_view_command(self, llmobs, tracer):
        raw_path = "/memories/alice@example.com/notes.md"
        span = self._run_anthropic_tagging(
            tracer,
            tools=[{"type": "memory_20250818", "name": "memory"}],
            content_blocks=[
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "memory",
                    "input": {"command": "view", "path": raw_path},
                }
            ],
        )
        meta = get_llmobs_metadata(span)
        assert meta["memory"]["operation"] == "get_memory"
        assert meta["memory"]["provider"] == "anthropic"
        assert meta["memory"]["record_ids"] == [hash_identifier(raw_path)]
        assert meta["memory"]["schema_version"] == "1"
        assert "memory_tool_calls" not in meta
        assert "alice@example.com" not in json.dumps(meta)
        assert raw_path not in json.dumps(meta)
        tags = get_llmobs_tags(span)
        assert tags["memory_operation"] == "get_memory"
        assert tags["memory_provider"] == "anthropic"
        assert tags["memory_detection_source"] == "explicit"
        metrics = get_llmobs_metrics(span)
        assert metrics["memory_record_count"] == 1.0
        assert "memory_hit" not in metrics
        assert metrics["input_tokens"] == 100

    def test_create_command_hashes_content_and_measures_bytes(self, llmobs, tracer):
        secret_note = "Confidential project roadmap 2026"
        span = self._run_anthropic_tagging(
            tracer,
            tools=[{"type": "memory_20250818", "name": "memory"}],
            content_blocks=[
                {
                    "type": "tool_use",
                    "id": "toolu_2",
                    "name": "memory",
                    "input": {"command": "create", "path": "/memories/plan.md", "file_text": secret_note},
                }
            ],
        )
        meta = get_llmobs_metadata(span)
        assert meta["memory"]["operation"] == "create_memory"
        assert meta["memory"]["write_outcome"] == "add"
        assert meta["memory"]["content_hashes"] == [hash_content(secret_note)]
        assert "memory_tool_calls" not in meta
        assert secret_note not in json.dumps(meta)
        metrics = get_llmobs_metrics(span)
        assert metrics["memory_record_count"] == 1.0
        assert metrics["memory_pre_write_bytes"] == 0.0
        assert metrics["memory_post_write_bytes"] == float(len(secret_note.encode("utf-8")))
        assert metrics["memory_size_delta_bytes"] == float(len(secret_note.encode("utf-8")))
        assert "memory_compaction_ratio" not in metrics

    def test_str_replace_shrinkage_delta(self, llmobs, tracer):
        old_str = "A" * 100
        new_str = "B" * 25
        span = self._run_anthropic_tagging(
            tracer,
            tools=[{"type": "memory_20250818", "name": "memory"}],
            content_blocks=[
                {
                    "type": "tool_use",
                    "id": "toolu_3",
                    "name": "memory",
                    "input": {
                        "command": "str_replace",
                        "path": "/memories/profile.md",
                        "old_str": old_str,
                        "new_str": new_str,
                    },
                }
            ],
        )
        meta = get_llmobs_metadata(span)
        assert meta["memory"]["operation"] == "update_memory"
        assert meta["memory"]["write_outcome"] == "update"
        assert meta["memory"]["record_ids"] == [hash_identifier("/memories/profile.md")]
        assert "memory_tool_calls" not in meta
        assert old_str not in json.dumps(meta)
        assert new_str not in json.dumps(meta)
        metrics = get_llmobs_metrics(span)
        assert metrics["memory_pre_write_bytes"] == 100.0
        assert metrics["memory_post_write_bytes"] == 25.0
        assert metrics["memory_size_delta_bytes"] == -75.0
        assert "memory_compaction_ratio" not in metrics

    def test_delete_command_and_multiple_calls(self, llmobs, tracer):
        span = self._run_anthropic_tagging(
            tracer,
            tools=[{"type": "memory_20250818", "name": "memory"}],
            content_blocks=[
                {
                    "type": "tool_use",
                    "id": "toolu_4",
                    "name": "memory",
                    "input": {"command": "view", "path": "/memories/old.md"},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_5",
                    "name": "memory",
                    "input": {"command": "delete", "path": "/memories/old.md"},
                },
            ],
        )
        meta = get_llmobs_metadata(span)
        assert meta["memory"]["operation"] == "delete_memory"
        assert meta["memory"]["write_outcome"] == "delete"
        assert "memory_tool_calls" not in meta
        assert meta["memory"]["record_ids"] == [hash_identifier("/memories/old.md")] * 2
        assert get_llmobs_metrics(span)["memory_record_count"] == 2.0

    def test_multicall_write_precedence_and_paired_byte_deltas(self, llmobs, tracer):
        # str_replace followed by view must stay update_memory with write_outcome="update".
        span = self._run_anthropic_tagging(
            tracer,
            tools=[{"type": "memory_20250818", "name": "memory"}],
            content_blocks=[
                {
                    "type": "tool_use",
                    "id": "toolu_a",
                    "name": "memory",
                    "input": {
                        "command": "str_replace",
                        "path": "/memories/profile.md",
                        "old_str": "A" * 300,
                        "new_str": "B" * 50,
                    },
                },
                {
                    "type": "tool_use",
                    "id": "toolu_b",
                    "name": "memory",
                    "input": {"command": "view", "path": "/memories/notes.md"},
                },
            ],
        )
        meta = get_llmobs_metadata(span)
        assert meta["memory"]["operation"] == "update_memory"
        assert meta["memory"]["write_outcome"] == "update"
        metrics = get_llmobs_metrics(span)
        assert metrics["memory_pre_write_bytes"] == 300.0
        assert metrics["memory_post_write_bytes"] == 50.0
        assert metrics["memory_size_delta_bytes"] == -250.0
        assert "memory_compaction_ratio" not in metrics

        # str_replace + insert must not corrupt paired str_replace byte delta with unpaired insert bytes.
        span_insert = self._run_anthropic_tagging(
            tracer,
            tools=[{"type": "memory_20250818", "name": "memory"}],
            content_blocks=[
                {
                    "type": "tool_use",
                    "id": "toolu_c",
                    "name": "memory",
                    "input": {
                        "command": "str_replace",
                        "path": "/memories/profile.md",
                        "old_str": "A" * 300,
                        "new_str": "B" * 50,
                    },
                },
                {
                    "type": "tool_use",
                    "id": "toolu_d",
                    "name": "memory",
                    "input": {
                        "command": "insert",
                        "path": "/memories/profile.md",
                        "insert_text": "C" * 300,
                    },
                },
            ],
        )
        assert get_llmobs_metadata(span_insert)["memory"]["operation"] == "update_memory"
        insert_metrics = get_llmobs_metrics(span_insert)
        assert insert_metrics["memory_pre_write_bytes"] == 300.0
        assert insert_metrics["memory_post_write_bytes"] == 50.0
        assert insert_metrics["memory_size_delta_bytes"] == -250.0

        # create + str_replace aggregates only the primary operation's paired bytes.
        span_create_replace = self._run_anthropic_tagging(
            tracer,
            tools=[{"type": "memory_20250818", "name": "memory"}],
            content_blocks=[
                {
                    "type": "tool_use",
                    "id": "toolu_e",
                    "name": "memory",
                    "input": {"command": "create", "path": "/memories/new.md", "file_text": "X" * 400},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_f",
                    "name": "memory",
                    "input": {
                        "command": "str_replace",
                        "path": "/memories/profile.md",
                        "old_str": "A" * 300,
                        "new_str": "B" * 50,
                    },
                },
            ],
        )
        assert get_llmobs_metadata(span_create_replace)["memory"]["operation"] == "update_memory"
        cr_metrics = get_llmobs_metrics(span_create_replace)
        assert cr_metrics["memory_pre_write_bytes"] == 300.0
        assert cr_metrics["memory_post_write_bytes"] == 50.0
        assert cr_metrics["memory_size_delta_bytes"] == -250.0

    def test_custom_tool_named_memory_without_memory_type_ignored(self, llmobs, tracer):
        span = self._run_anthropic_tagging(
            tracer,
            tools=[{"name": "memory", "input_schema": {"type": "object"}}],
            content_blocks=[
                {
                    "type": "tool_use",
                    "id": "toolu_custom",
                    "name": "memory",
                    "input": {"command": "view", "path": "/users/alice@example.com"},
                }
            ],
        )
        meta = get_llmobs_metadata(span) or {}
        assert "memory" not in meta
        assert "memory_tool_calls" not in meta
        assert "memory_operation" not in (get_llmobs_tags(span) or {})

    def test_memory_tool_declared_in_extra_body_detected(self, llmobs, tracer):
        span = self._run_anthropic_tagging(
            tracer,
            tools=None,
            extra_body={"tools": [{"type": "memory_20250818", "name": "memory"}]},
            content_blocks=[
                {
                    "type": "tool_use",
                    "id": "toolu_eb",
                    "name": "memory",
                    "input": {"command": "view", "path": "/memories/notes.md"},
                }
            ],
        )
        meta = get_llmobs_metadata(span)
        assert meta["memory"]["operation"] == "get_memory"
        assert meta["memory"]["record_ids"] == [hash_identifier("/memories/notes.md")]

    def test_malformed_non_string_block_name_does_not_abort_span_tagging(self, llmobs, tracer):
        span = self._run_anthropic_tagging(
            tracer,
            tools=[{"type": "memory_20250818", "name": "memory"}],
            content_blocks=[
                {
                    "type": "tool_use",
                    "id": "toolu_bad",
                    "name": {"unhashable": 1},
                    "input": {"command": "view", "path": "/memories/notes.md"},
                }
            ],
        )
        metrics = get_llmobs_metrics(span)
        assert metrics["input_tokens"] == 100
        assert metrics["output_tokens"] == 40
        assert "memory" not in (get_llmobs_metadata(span) or {})

    def test_non_memory_tool_does_not_attach_memory_metadata(self, llmobs, tracer):
        span = self._run_anthropic_tagging(
            tracer,
            tools=[{"type": "memory_20250818", "name": "memory"}],
            content_blocks=[{"type": "text", "text": "Hello!"}],
        )
        meta = get_llmobs_metadata(span) or {}
        assert "memory" not in meta
        assert "memory_operation" not in (get_llmobs_tags(span) or {})


@dataclass
class _MockStoreItem:
    namespace: tuple[str, ...]
    key: str
    value: dict[str, Any]
    score: Optional[float] = None


class _MockBaseStore:
    def __init__(self) -> None:
        self._data: dict[tuple[tuple[str, ...], str], _MockStoreItem] = {}

    def put(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self._data[(namespace, key)] = _MockStoreItem(namespace=namespace, key=key, value=value)

    def get(self, namespace: tuple[str, ...], key: str) -> Optional[_MockStoreItem]:
        return self._data.get((namespace, key))

    def search(self, namespace_prefix: tuple[str, ...], *, query: Optional[str] = None) -> list[_MockStoreItem]:
        out = []
        for (ns, _), item in self._data.items():
            if ns[: len(namespace_prefix)] == namespace_prefix:
                out.append(_MockStoreItem(namespace=item.namespace, key=item.key, value=item.value, score=0.91))
        return out

    def delete(self, namespace: tuple[str, ...], key: str) -> None:
        self._data.pop((namespace, key), None)

    async def aput(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.put(namespace, key, value)

    async def aget(self, namespace: tuple[str, ...], key: str) -> Optional[_MockStoreItem]:
        return self.get(namespace, key)

    async def asearch(self, namespace_prefix: tuple[str, ...], *, query: Optional[str] = None) -> list[_MockStoreItem]:
        return self.search(namespace_prefix, query=query)

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        self.delete(namespace, key)


def _install_mock_langgraph(monkeypatch, include_store: bool = True):
    """Register minimal fake langgraph modules so real patch()/unpatch() runs without langgraph installed."""

    class _DummyPregel:
        def stream(self, *args, **kwargs):
            return iter([])

        def astream(self, *args, **kwargs):
            return iter([])

    class _DummyPregelLoop:
        def tick(self, *args, **kwargs):
            return False

    class _DummyRunnableSeq:
        def invoke(self, *args, **kwargs):
            return None

        async def ainvoke(self, *args, **kwargs):
            return None

        def astream(self, *args, **kwargs):
            return iter([])

    async def _dummy_consume_aiter(*args, **kwargs):
        return None

    class _LocalBaseStore(_MockBaseStore):
        pass

    lg = types.ModuleType("langgraph")
    lg_version = types.ModuleType("langgraph.version")
    lg_version.__version__ = "0.6.0"
    lg_prebuilt = types.ModuleType("langgraph.prebuilt")
    lg_prebuilt.create_react_agent = lambda *args, **kwargs: None
    lg.prebuilt = lg_prebuilt
    lg_pregel = types.ModuleType("langgraph.pregel")
    lg_pregel.Pregel = _DummyPregel
    lg_pregel_loop = types.ModuleType("langgraph.pregel._loop")
    lg_pregel_loop.PregelLoop = _DummyPregelLoop
    lg_internal = types.ModuleType("langgraph._internal")
    lg_internal_runnable = types.ModuleType("langgraph._internal._runnable")
    lg_internal_runnable.RunnableSeq = _DummyRunnableSeq
    lg_internal_runnable._consume_aiter = _dummy_consume_aiter
    lg._internal = lg_internal
    lg_internal._runnable = lg_internal_runnable

    modules: dict[str, Any] = {
        "langgraph": lg,
        "langgraph.version": lg_version,
        "langgraph.prebuilt": lg_prebuilt,
        "langgraph.pregel": lg_pregel,
        "langgraph.pregel._loop": lg_pregel_loop,
        "langgraph._internal": lg_internal,
        "langgraph._internal._runnable": lg_internal_runnable,
    }
    if include_store:
        lg_store = types.ModuleType("langgraph.store")
        lg_store_base = types.ModuleType("langgraph.store.base")
        lg_store_base.BaseStore = _LocalBaseStore
        modules["langgraph.store"] = lg_store
        modules["langgraph.store.base"] = lg_store_base
    else:
        modules["langgraph.store"] = None
        modules["langgraph.store.base"] = None

    for mod_name, mod in modules.items():
        monkeypatch.setitem(sys.modules, mod_name, mod)
    monkeypatch.delitem(sys.modules, "ddtrace.contrib.internal.langgraph.patch", raising=False)
    patch_mod = importlib.import_module("ddtrace.contrib.internal.langgraph.patch")
    return patch_mod, _LocalBaseStore


class TestLangGraphBaseStoreInstrumentation:
    def test_patch_unwrap_and_idempotency(self, monkeypatch):
        from wrapt import BoundFunctionWrapper
        from wrapt import FunctionWrapper

        patch_mod, base_store_cls = _install_mock_langgraph(monkeypatch, include_store=True)
        methods = ("get", "aget", "search", "asearch", "put", "aput", "delete", "adelete")
        try:
            patch_mod.patch()
            assert getattr(base_store_cls, "_datadog_patch", False) is True
            for m in methods:
                wrapped = getattr(base_store_cls, m)
                assert isinstance(wrapped, (FunctionWrapper, BoundFunctionWrapper))
                assert not isinstance(wrapped.__wrapped__, (FunctionWrapper, BoundFunctionWrapper))

            # Second patch() must not double-wrap.
            patch_mod.patch()
            for m in methods:
                wrapped = getattr(base_store_cls, m)
                assert not isinstance(wrapped.__wrapped__, (FunctionWrapper, BoundFunctionWrapper))
        finally:
            patch_mod.unpatch()

        assert getattr(base_store_cls, "_datadog_patch", False) is False
        for m in methods:
            assert not isinstance(getattr(base_store_cls, m), (FunctionWrapper, BoundFunctionWrapper))

    def test_patch_noop_when_langgraph_store_unavailable(self, monkeypatch):
        patch_mod, _ = _install_mock_langgraph(monkeypatch, include_store=False)
        try:
            patch_mod.patch()
        finally:
            patch_mod.unpatch()

    def test_sync_store_operations_and_namespace_scoping(self, llmobs, tracer, test_spans, monkeypatch):
        from ddtrace.llmobs._contrib.langgraph import listen
        from ddtrace.llmobs._contrib.langgraph import unlisten
        import ddtrace.trace as ddtrace_trace

        monkeypatch.setattr(ddtrace_trace, "tracer", tracer)
        patch_mod, base_store_cls = _install_mock_langgraph(monkeypatch, include_store=True)
        store = base_store_cls()
        listen()
        patch_mod.patch()
        try:
            # Standalone root put uses "workflow".
            store.put(("orgs", "acme-corp", "kb"), "k0", {"policy": "no-pii"})

            with llmobs.agent("support-agent"):
                store.put(("users", "alice@example.com", "memories"), "pref-1", {"drink": "espresso"})
                hit = store.get(("users", "alice@example.com", "memories"), "pref-1")
                assert hit is not None and hit.value == {"drink": "espresso"}
                miss = store.get(("sessions", "sess-7"), "missing-key")
                assert miss is None
                results = store.search(("users", "alice@example.com"), query="favorite drink")
                assert len(results) == 1
                store.delete(("agents", "agent-7"), "pref-1")
        finally:
            patch_mod.unpatch()
            unlisten()

        spans = [s for trace in test_spans.pop_traces() for s in trace if get_llmobs_span_kind(s)]
        by_op = {}
        for s in spans:
            meta = get_llmobs_metadata(s) or {}
            if "memory" in meta:
                by_op.setdefault(meta["memory"]["operation"], []).append(s)

        # Standalone root put -> workflow with org tenant_hash.
        root_put = by_op["upsert_memory"][0]
        assert get_llmobs_parent_id(root_put) == ROOT_PARENT_ID
        assert get_llmobs_span_kind(root_put) == "workflow"
        root_mem = get_llmobs_metadata(root_put)["memory"]
        assert root_mem["scope"] == "org"
        assert root_mem["tenant_hash"] == hash_identifier("acme-corp")
        assert root_mem["provider"] == "langgraph"
        assert "acme-corp" not in json.dumps(get_llmobs_metadata(root_put))

        # Child put -> tool with user principal_hash and no raw PII.
        child_put = by_op["upsert_memory"][1]
        assert get_llmobs_span_kind(child_put) == "tool"
        put_mem = get_llmobs_metadata(child_put)["memory"]
        assert put_mem["scope"] == "user"
        assert put_mem["principal_hash"] == hash_identifier("alice@example.com")
        assert put_mem["record_ids"] == ["pref-1"]
        assert "alice@example.com" not in json.dumps(get_llmobs_metadata(child_put))
        assert "espresso" not in json.dumps(get_llmobs_metadata(child_put))
        assert "espresso" in (get_llmobs_output_value(child_put) or "")

        # Child get hit & miss -> retrieval.
        get_hit, get_miss = by_op["get_memory"]
        assert get_llmobs_span_kind(get_hit) == "retrieval"
        assert get_llmobs_metrics(get_hit)["memory_hit"] == 1.0
        assert get_llmobs_output_documents(get_hit)[0]["id"] == "pref-1"
        assert get_llmobs_span_kind(get_miss) == "retrieval"
        assert get_llmobs_metrics(get_miss)["memory_hit"] == 0.0
        assert get_llmobs_metadata(get_miss)["memory"]["scope"] == "session"
        assert get_llmobs_metadata(get_miss)["memory"]["principal_hash"] == hash_identifier("sess-7")

        # Child search -> retrieval with score & documents.
        search_span = by_op["search_memory"][0]
        assert get_llmobs_span_kind(search_span) == "retrieval"
        assert get_llmobs_input_value(search_span) == "favorite drink"
        assert get_llmobs_metrics(search_span)["memory_top_score"] == pytest.approx(0.91)
        assert get_llmobs_output_documents(search_span)[0]["score"] == pytest.approx(0.91)
        assert get_llmobs_tags(search_span)["memory_provider"] == "langgraph"

        # Child delete -> tool with agent scope.
        del_span = by_op["delete_memory"][0]
        assert get_llmobs_span_kind(del_span) == "tool"
        del_mem = get_llmobs_metadata(del_span)["memory"]
        assert del_mem["scope"] == "agent"
        assert del_mem["principal_hash"] == hash_identifier("agent-7")
        assert del_mem["write_outcome"] == "delete"

    async def test_async_store_operations(self, llmobs, tracer, test_spans, monkeypatch):
        from ddtrace.llmobs._contrib.langgraph import listen
        from ddtrace.llmobs._contrib.langgraph import unlisten
        import ddtrace.trace as ddtrace_trace

        monkeypatch.setattr(ddtrace_trace, "tracer", tracer)
        patch_mod, base_store_cls = _install_mock_langgraph(monkeypatch, include_store=True)
        store = base_store_cls()
        listen()
        patch_mod.patch()
        try:
            with llmobs.agent("async-agent"):
                await store.aput(("users", "bob"), "k1", {"lang": "python"})
                got = await store.aget(("users", "bob"), "k1")
                assert got is not None
                found = await store.asearch(("users", "bob"), query="language")
                assert len(found) == 1
                await store.adelete(("users", "bob"), "k1")
        finally:
            patch_mod.unpatch()
            unlisten()

        spans = [s for trace in test_spans.pop_traces() for s in trace if get_llmobs_span_kind(s)]
        mem_ops = [
            get_llmobs_metadata(s)["memory"]["operation"] for s in spans if "memory" in (get_llmobs_metadata(s) or {})
        ]
        assert mem_ops == ["upsert_memory", "get_memory", "search_memory", "delete_memory"]

    def test_namespace_hashing_prevents_pii_leakage(self):
        ns, scope, principal, tenant = _parse_store_namespace(("alice@example.com", "memories"))
        assert ns == f"{hash_identifier('alice@example.com')}/{hash_identifier('memories')}"
        assert scope is None and principal is None and tenant is None
        assert "alice@example.com" not in (ns or "")

        ns2, scope2, principal2, tenant2 = _parse_store_namespace(("memories", "alice@example.com"))
        assert ns2 == f"{hash_identifier('memories')}/{hash_identifier('alice@example.com')}"
        assert scope2 is None and principal2 is None and tenant2 is None
        assert "alice@example.com" not in (ns2 or "")

        ns3, scope3, principal3, tenant3 = _parse_store_namespace(("orgs", "acme-corp", "users", "alice@example.com"))
        assert ns3 == f"orgs/{hash_identifier('acme-corp')}/users/{hash_identifier('alice@example.com')}"
        assert scope3 == "org"
        assert tenant3 == "acme-corp"
        assert principal3 == "alice@example.com"
        assert "acme-corp" not in (ns3 or "")
        assert "alice@example.com" not in (ns3 or "")

        ns4, scope4, principal4, tenant4 = _parse_store_namespace("users/alice@example.com")
        assert ns4 == f"users/{hash_identifier('alice@example.com')}"
        assert scope4 == "user"
        assert principal4 == "alice@example.com"
        assert tenant4 is None
        assert "alice@example.com" not in (ns4 or "")

    def test_nested_multitenant_namespace_populates_both_hashes(self, llmobs, tracer, test_spans, monkeypatch):
        from ddtrace.llmobs._contrib.langgraph import listen
        from ddtrace.llmobs._contrib.langgraph import unlisten
        import ddtrace.trace as ddtrace_trace

        monkeypatch.setattr(ddtrace_trace, "tracer", tracer)
        patch_mod, base_store_cls = _install_mock_langgraph(monkeypatch, include_store=True)
        store = base_store_cls()
        listen()
        patch_mod.patch()
        try:
            with llmobs.agent("tenant-agent"):
                store.put(("orgs", "acme-corp", "users", "alice@example.com"), "k1", {"ok": True})
                store.put(("alice@example.com", "memories"), "k2", {"ok": True})
        finally:
            patch_mod.unpatch()
            unlisten()

        spans = [s for trace in test_spans.pop_traces() for s in trace if "memory" in (get_llmobs_metadata(s) or {})]
        assert len(spans) == 2
        mt_meta = get_llmobs_metadata(spans[0])["memory"]
        assert mt_meta["tenant_hash"] == hash_identifier("acme-corp")
        assert mt_meta["principal_hash"] == hash_identifier("alice@example.com")
        assert "acme-corp" not in json.dumps(mt_meta)
        assert "alice@example.com" not in json.dumps(mt_meta)

        unprefixed_meta = get_llmobs_metadata(spans[1])["memory"]
        assert "alice@example.com" not in json.dumps(unprefixed_meta)

    def test_search_does_not_exhaust_generator_response(self, llmobs, tracer, test_spans, monkeypatch):
        from ddtrace.llmobs._contrib.langgraph import listen
        from ddtrace.llmobs._contrib.langgraph import unlisten
        import ddtrace.trace as ddtrace_trace

        monkeypatch.setattr(ddtrace_trace, "tracer", tracer)
        patch_mod, base_store_cls = _install_mock_langgraph(monkeypatch, include_store=True)
        expected_items = [
            _MockStoreItem(namespace=("users", "u1"), key="k1", value={"a": 1}, score=0.9),
            _MockStoreItem(namespace=("users", "u1"), key="k2", value={"a": 2}, score=0.8),
        ]
        monkeypatch.setattr(base_store_cls, "search", lambda self, ns, query=None: (item for item in expected_items))
        store = base_store_cls()
        listen()
        patch_mod.patch()
        try:
            with llmobs.agent("gen-agent"):
                gen = store.search(("users", "u1"), query="prefs")
                received = list(gen)
        finally:
            patch_mod.unpatch()
            unlisten()

        assert received == expected_items

    def test_errored_put_and_delete_do_not_emit_write_metrics_or_output(self, llmobs, tracer, test_spans, monkeypatch):
        from ddtrace.llmobs._contrib.langgraph import listen
        from ddtrace.llmobs._contrib.langgraph import unlisten
        import ddtrace.trace as ddtrace_trace

        monkeypatch.setattr(ddtrace_trace, "tracer", tracer)
        patch_mod, base_store_cls = _install_mock_langgraph(monkeypatch, include_store=True)

        def _failing_put(self, namespace, key, value):
            raise ConnectionError("store unreachable")

        def _failing_delete(self, namespace, key):
            raise ConnectionError("store unreachable")

        monkeypatch.setattr(base_store_cls, "put", _failing_put)
        monkeypatch.setattr(base_store_cls, "delete", _failing_delete)
        store = base_store_cls()
        listen()
        patch_mod.patch()
        try:
            with llmobs.agent("err-agent"):
                with pytest.raises(ConnectionError):
                    store.put(("users", "u1"), "k1", {"a": 1})
                with pytest.raises(ConnectionError):
                    store.delete(("users", "u1"), "k1")
        finally:
            patch_mod.unpatch()
            unlisten()

        spans = [s for trace in test_spans.pop_traces() for s in trace if "memory" in (get_llmobs_metadata(s) or {})]
        put_span, del_span = spans
        assert put_span.error == 1
        put_metrics = get_llmobs_metrics(put_span) or {}
        assert "memory_record_count" not in put_metrics
        assert "memory_post_write_bytes" not in put_metrics
        assert get_llmobs_output_value(put_span) is None
        assert "k1" in (get_llmobs_input_value(put_span) or "")

        assert del_span.error == 1
        del_metrics = get_llmobs_metrics(del_span) or {}
        assert "memory_record_count" not in del_metrics
        assert "write_outcome" not in get_llmobs_metadata(del_span)["memory"]
        assert "record_ids" not in get_llmobs_metadata(del_span)["memory"]


class TestImportLayeringAndLazyImports:
    def _imported_modules(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        return imported

    def test_langgraph_patch_does_not_import_llmobs(self):
        patch_mod = importlib.import_module("ddtrace.contrib.internal.langgraph.patch")
        imports = self._imported_modules(Path(inspect.getfile(patch_mod)))
        assert not any(mod == "ddtrace.llmobs" or mod.startswith("ddtrace.llmobs.") for mod in imports)

    def test_memory_module_does_not_import_asyncio_or_concurrent_futures(self):
        mem_mod = importlib.import_module("ddtrace.llmobs._memory")
        imports = self._imported_modules(Path(inspect.getfile(mem_mod)))
        for forbidden in ("asyncio", "concurrent", "concurrent.futures"):
            assert not any(mod == forbidden or mod.startswith(f"{forbidden}.") for mod in imports)
