#!/usr/bin/env python3
"""Fetch and process a single crash log from Datadog by UUID.

Usage:
    python fetch_crash.py <uuid> [--lib-language python] [--tracer-version '*'] [--org-id '*'] [--days 14]

Requires DD_API_KEY and DD_APP_KEY environment variables.
Requires: pip install datadog-api-client
"""

import argparse
import ctypes.util
import json
import re
import sys
from collections import OrderedDict
from ctypes import CDLL, c_char_p, c_int, c_void_p
from datetime import datetime, timedelta
from time import sleep

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.exceptions import ApiException
from datadog_api_client.v2.api import logs_api
from datadog_api_client.v2.model.logs_list_request import LogsListRequest
from datadog_api_client.v2.model.logs_list_request_page import LogsListRequestPage
from datadog_api_client.v2.model.logs_query_filter import LogsQueryFilter
from datadog_api_client.v2.model.logs_sort import LogsSort


class _DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


# ---------------------------------------------------------------------------
# C++ symbol demangling
# ---------------------------------------------------------------------------

def _init_demangler():
    lib = ctypes.util.find_library("c++")
    if lib:
        cxx = CDLL(lib)
    else:
        for name in ("libc++.so", "libc++.dylib", "libstdc++.so.6"):
            try:
                cxx = CDLL(name)
                break
            except OSError:
                continue
        else:
            return None

    fn = cxx.__cxa_demangle
    fn.argtypes = [c_char_p, c_void_p, c_void_p, c_int]
    fn.restype = c_char_p
    return fn


_demangler = None
try:
    _demangler = _init_demangler()
except Exception:
    pass


def demangle(name):
    if not _demangler or not name:
        return name
    try:
        result = _demangler(name.encode("utf-8"), None, None, 0)
        if result:
            return result.decode("utf-8")
    except Exception:
        pass
    return name


# ---------------------------------------------------------------------------
# /proc/self/maps parsing
# ---------------------------------------------------------------------------

_MAPS_RE = re.compile(
    r"([0-9a-fA-F]+)-([0-9a-fA-F]+)\s+([rwxsp-]{4})\s+([0-9a-fA-F]+)"
    r"\s+[0-9a-fA-F]+:[0-9a-fA-F]+\s+\d+\s*(.*)?",
)


def _parse_proc_maps(lines):
    entries = []
    for line in lines:
        if not line:
            continue
        m = _MAPS_RE.match(line)
        if not m:
            continue
        entries.append(
            {
                "start": int(m.group(1), 16),
                "end": int(m.group(2), 16),
                "offset": int(m.group(4), 16),
                "file": (m.group(5).strip() or "[anonymous]"),
            }
        )
    return entries


def _resolve_address(ip, maps):
    for entry in maps:
        if entry["start"] <= ip < entry["end"]:
            return {
                "binary": entry["file"].rsplit("/", 1)[-1],
                "binary_path": entry["file"],
                "offset": hex(ip - entry["start"] + entry["offset"]),
            }
    return None


# ---------------------------------------------------------------------------
# Stack frame processing
# ---------------------------------------------------------------------------


def _process_frame(frame, maps):
    try:
        ip = int(frame["ip"], 16)
        sp = int(frame["sp"], 16)
    except (ValueError, KeyError):
        return []

    # Prefer inline `path` / `relative_address` (new crashtracker format)
    # over /proc/self/maps resolution.
    if "path" in frame:
        binary_path = frame["path"]
        binary = binary_path.rsplit("/", 1)[-1]
        offset = frame.get("relative_address", "")
    else:
        resolved = _resolve_address(ip, maps)
        if resolved:
            binary_path = resolved["binary_path"]
            binary = resolved["binary"]
            offset = resolved["offset"]
        else:
            binary_path = ""
            binary = ""
            offset = ""

    # Build the names list from whichever field is present.
    if "function" in frame and not frame.get("names"):
        names = [{"name": frame["function"]}]
    elif frame.get("names"):
        names = frame["names"]
    else:
        names = [{}]

    frames = []
    for idx, name_entry in enumerate(names):
        symbol = demangle(name_entry.get("name", ""))
        if len(names) > 1:
            symbol += " (top)" if idx == 0 else " (inlined)"

        f = {
            "ip": hex(ip),
            "sp": hex(sp),
            "symbol": symbol or hex(ip),
            "source_file": name_entry.get("filename", ""),
            "source_line": str(name_entry.get("lineno", 0)),
        }
        if binary:
            f["binary"] = binary
            f["binary_path"] = binary_path
        if offset:
            f["offset"] = offset
        frames.append(f)

    return frames


# ---------------------------------------------------------------------------
# Crash log processing
# ---------------------------------------------------------------------------


def process_crash_log(raw_attrs):
    """Process raw crash log attributes into a structured report."""
    if "error" not in raw_attrs or "stack" not in raw_attrs["error"]:
        return None

    maps_lines = raw_attrs.get("files", {}).get("/proc/self/maps", [])
    maps = _parse_proc_maps(maps_lines)

    stack_data = raw_attrs["error"]["stack"]
    if isinstance(stack_data, str):
        stack_data = json.loads(stack_data)

    if isinstance(stack_data, dict):
        if "frames" in stack_data:
            raw_frames = stack_data["frames"]
        elif stack_data.get("incomplete"):
            raw_frames = [
                {"ip": "0x0", "sp": "0x0", "names": [{"name": "INCOMPLETE_STACK"}]}
            ]
        else:
            raw_frames = []
    elif isinstance(stack_data, list):
        raw_frames = stack_data
    else:
        raw_frames = []

    processed_frames = []
    for frame in raw_frames:
        processed_frames.extend(_process_frame(frame, maps))

    tags = raw_attrs.get("metadata", {}).get("tags", [])
    tag_map = {}
    for tag in tags:
        if ":" in tag:
            k, v = tag.split(":", 1)
            tag_map[k] = v

    error = raw_attrs.get("error", {})
    sig_info = raw_attrs.get("sig_info", {})
    host = raw_attrs.get("host", {})
    os_info = raw_attrs.get("os_info", {})

    result = OrderedDict()
    result["tracer_version"] = raw_attrs.get("tracer_version", "unknown")
    result["runtime_version"] = (
        raw_attrs.get("language_version")
        or tag_map.get("runtime_version")
        or "unknown"
    )
    result["service"] = (
        raw_attrs.get("instrumented_service")
        or tag_map.get("service")
        or "unknown"
    )
    result["org_id"] = raw_attrs.get("org_id", "unknown")
    result["platform"] = os_info.get("os_type") or host.get("os", "unknown")
    result["architecture"] = os_info.get("architecture") or host.get("arch") or "unknown"

    result["signal"] = sig_info.get("si_signo_human_readable") or "unknown"
    result["signal_detail"] = error.get("message", "")
    result["error_kind"] = error.get("kind", "")
    result["thread_name"] = error.get("thread_name", "")

    proc_info = raw_attrs.get("proc_info", {})
    if proc_info:
        result["pid"] = proc_info.get("pid")
        result["tid"] = proc_info.get("tid")

    result["tags"] = tag_map
    result["stack"] = processed_frames

    other_threads = error.get("threads")
    if isinstance(other_threads, dict):
        thread_list = other_threads.get("threads", [])
        result["thread_count"] = other_threads.get("count", len(thread_list))
    elif isinstance(other_threads, list):
        result["thread_count"] = len(other_threads)

    runtime_stack = raw_attrs.get("experimental", {}).get("runtime_stack", {})
    if isinstance(runtime_stack, dict):
        if runtime_stack.get("stacktrace_string"):
            result["python_stack"] = runtime_stack["stacktrace_string"]
        elif runtime_stack.get("frames"):
            result["python_stack"] = runtime_stack["frames"]

    if maps:
        seen = set()
        binary_mappings = []
        for entry in maps:
            name = entry["file"]
            if name not in seen and not name.startswith("["):
                seen.add(name)
                binary_mappings.append(
                    f"  {hex(entry['start'])}-{hex(entry['end'])} {name}"
                )
        result["binary_mappings_summary"] = binary_mappings[:50]

    return result


# ---------------------------------------------------------------------------
# Datadog API
# ---------------------------------------------------------------------------


def fetch_crash_by_uuid(uuid, lib_language="python", tracer_version="*", org_id="*", days=14):
    """Fetch a single crash log from Datadog by UUID and return processed data."""
    org_id_str = "*" if org_id == "*" else str(org_id)

    query = f"uuid:{uuid}"

    extra_filters = []
    if lib_language != "*":
        extra_filters.append(f"@lib_language:{lib_language}")
    if org_id_str != "*":
        extra_filters.append(f"@org_id:{org_id_str}")
    if tracer_version != "*":
        extra_filters.append(f"@tracer_version:{tracer_version}")
    if extra_filters:
        query += " " + " ".join(extra_filters)

    print(f"Query: {query}", file=sys.stderr)

    configuration = Configuration()
    configuration.request_timeout = (10, 120)
    end_time = datetime.now()
    start_time = end_time - timedelta(days=days)

    with ApiClient(configuration) as api_client:
        api_instance = logs_api.LogsApi(api_client)
        body = LogsListRequest(
            filter=LogsQueryFilter(
                query=query,
                _from=str(int(start_time.timestamp() * 1000)),
                to=str(int(end_time.timestamp() * 1000)),
            ),
            sort=LogsSort("timestamp_descending"),
            page=LogsListRequestPage(limit=1),
        )

        response = None
        for attempt in range(3):
            try:
                response = api_instance.list_logs(body=body)
                break
            except ApiException as e:
                if e.status != 500 or attempt == 2:
                    print(f"Datadog API error: {e.status} - {e.reason}", file=sys.stderr)
                    sys.exit(1)
                sleep(2**attempt)

        if not response or not response.data:
            print(f"No crash log found for UUID: {uuid}", file=sys.stderr)
            print(f"Query used: {query}", file=sys.stderr)
            sys.exit(1)

        log = response.data[0]
        raw_attrs = log.attributes.attributes
        log_id = log.id
        timestamp = log.attributes.timestamp.isoformat()

        processed = process_crash_log(raw_attrs)
        if processed is None:
            print(
                "Crash log found but could not be processed "
                "(missing error.stack or unexpected format).",
                file=sys.stderr,
            )
            json.dump(raw_attrs, sys.stderr, indent=2, cls=_DateTimeEncoder)
            sys.exit(1)

        processed["log_id"] = log_id
        processed["timestamp"] = timestamp
        processed["log_url"] = f"https://app.datadoghq.com/logs?event={log_id}"
        processed["query_used"] = query

        return processed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and process a single crash log from Datadog by UUID.",
    )
    parser.add_argument("uuid", help="Crash UUID to look up")
    parser.add_argument(
        "--lib-language",
        default="python",
        help="Library language filter (default: python)",
    )
    parser.add_argument(
        "--tracer-version",
        default="*",
        help="Tracer version filter (default: *)",
    )
    parser.add_argument(
        "--org-id",
        default="*",
        help="Org ID filter (default: *)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Days to look back (default: 14)",
    )
    args = parser.parse_args()

    result = fetch_crash_by_uuid(
        uuid=args.uuid,
        lib_language=args.lib_language,
        tracer_version=args.tracer_version,
        org_id=args.org_id,
        days=args.days,
    )

    json.dump(result, sys.stdout, indent=2, cls=_DateTimeEncoder)
    print()


if __name__ == "__main__":
    main()
