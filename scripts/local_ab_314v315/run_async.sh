#!/usr/bin/env bash
# scripts/local_ab_314v315/run_async.sh — long-lived asyncio 3.14 vs 3.15 profiling A/B.
#
# Sibling of run.sh / app.py smoke harness. Uses async_app.py (one event loop for
# the soak) instead of ThreadingHTTPServer + per-request asyncio.run.
# Reuses drive.py; same venv / PROFILING=0 / ps RSS/CPU / pprof meta pattern.
#
# Usage (from repo root or this dir):
#   DDTRACE_SRC=/path/to/#19272-tip ./scripts/local_ab_314v315/run_async.sh
#   DURATION=90 REUSE_VENV=/tmp/local314v315_.../venvs ./scripts/local_ab_314v315/run_async.sh
#   PROFILING=0 DURATION=90 DDTRACE_SRC=... ./scripts/local_ab_314v315/run_async.sh
#
# Tip: install the #19272 tip (faae7e3 or current PR head) for both arms — only
# the Python interpreter differs. Do not point DDTRACE_SRC at a chore-only branch
# that lacks the asyncio monitoring changes.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RUN_ID="${RUN_ID:-local314v315_async_$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_DIR="${RUN_DIR:-/tmp/${RUN_ID}}"
mkdir -p "${RUN_DIR}"/{venvs,pprof,logs}

PY314_BIN="${PY314_BIN:-/opt/homebrew/bin/python3.14}"
PY315_BIN="${PY315_BIN:-}"
if [[ -z "${PY315_BIN}" ]]; then
  for cand in \
    "${HOME}/.pyenv/versions/3.15.0a7/bin/python" \
    "${HOME}/.local/share/uv/python/cpython-3.15-macos-aarch64-none/bin/python3.15"; do
    if [[ -x "${cand}" ]]; then
      PY315_BIN="${cand}"
      break
    fi
  done
fi
: "${PY315_BIN:?set PY315_BIN to a Python 3.15 interpreter}"

DDTRACE_SRC="${DDTRACE_SRC:-${REPO_ROOT}}"
: "${DDTRACE_SRC:?set DDTRACE_SRC to a dd-trace-py checkout with #19272 tip}"

DDTRACE_REF="${DDTRACE_REF:-}"
PORT_A="${PORT_A:-18500}"
PORT_B="${PORT_B:-18501}"
DURATION="${DURATION:-90}"
CONCURRENCY="${CONCURRENCY:-4}"
UPLOAD_INTERVAL="${DD_PROFILING_UPLOAD_INTERVAL:-15}"
REUSE_VENV="${REUSE_VENV:-}"
PROFILING="${PROFILING:-1}"
case "${PROFILING}" in
  0|false|False|FALSE|off|OFF|no|NO) PROFILING_ENABLED=false ;;
  1|true|True|TRUE|on|ON|yes|YES) PROFILING_ENABLED=true ;;
  *)
    echo "ERROR: PROFILING must be 0/1 (got '${PROFILING}')" >&2
    exit 1
    ;;
esac

CORPUS="${SCRIPT_DIR}/async_corpus.txt"
DRIVE_PY="${SCRIPT_DIR}/drive.py"
APP_PY="${SCRIPT_DIR}/async_app.py"

for bin in "${PY314_BIN}" "${PY315_BIN}"; do
  if [[ ! -x "${bin}" ]]; then
    echo "ERROR: missing interpreter ${bin}" >&2
    exit 1
  fi
done

echo "=== local 314v315 ASYNC (dd-trace-py) ==="
echo "RUN_DIR=${RUN_DIR}"
echo "DDTRACE_SRC=${DDTRACE_SRC}"
echo "PROFILING=${PROFILING} (DD_PROFILING_ENABLED=${PROFILING_ENABLED})"
echo "A: ${PY314_BIN} ($("${PY314_BIN}" -V 2>&1)) port=${PORT_A}"
echo "B: ${PY315_BIN} ($("${PY315_BIN}" -V 2>&1)) port=${PORT_B}"
if [[ -n "${DDTRACE_REF}" ]]; then
  echo "checkout ${DDTRACE_REF} in ${DDTRACE_SRC}"
  git -C "${DDTRACE_SRC}" checkout --detach "${DDTRACE_REF}"
fi
echo "ddtrace HEAD: $(git -C "${DDTRACE_SRC}" rev-parse --short HEAD) $(git -C "${DDTRACE_SRC}" log -1 --oneline)"

_install() {
  local py="$1" venv="$2" label="$3"
  if [[ -n "${REUSE_VENV}" ]]; then
    local src_venv=""
    case "${label}" in
      A314) src_venv="${REUSE_VENV}/a314" ;;
      B315) src_venv="${REUSE_VENV}/b315" ;;
    esac
    if [[ -x "${src_venv}/bin/python" ]]; then
      echo ">>> [${label}] symlink reuse ${src_venv} -> ${venv}"
      rm -rf "${venv}"
      ln -s "${src_venv}" "${venv}"
      "${venv}/bin/python" -c "import ddtrace, sys; print('[${label}] ddtrace=' + ddtrace.__version__ + ' py=' + sys.version.split()[0])"
      return 0
    fi
  fi
  if [[ -x "${venv}/bin/python" ]] && "${venv}/bin/python" -c "import ddtrace" 2>/dev/null; then
    echo ">>> [${label}] reuse existing venv ${venv}"
    "${venv}/bin/python" -c "import ddtrace, sys; print('[${label}] ddtrace=' + ddtrace.__version__ + ' py=' + sys.version.split()[0])"
    return 0
  fi
  echo ">>> [${label}] venv ${venv}"
  "${py}" -m venv "${venv}"
  # shellcheck disable=SC1091
  source "${venv}/bin/activate"
  pip install -U pip setuptools wheel >/dev/null
  echo ">>> [${label}] pip install -e ${DDTRACE_SRC} (may take several minutes)"
  PIP_IGNORE_REQUIRES_PYTHON=1 pip install --ignore-requires-python --no-binary=wrapt \
    -e "${DDTRACE_SRC}" \
    2>&1 | tee "${RUN_DIR}/logs/pip_${label}.log" | tail -20
  python -c "import ddtrace, sys; print('[${label}] ddtrace=' + ddtrace.__version__ + ' py=' + sys.version.split()[0])"
  deactivate
}

_install "${PY314_BIN}" "${RUN_DIR}/venvs/a314" "A314"
_install "${PY315_BIN}" "${RUN_DIR}/venvs/b315" "B315"

_start() {
  local venv="$1" port="$2" label="$3" side="$4"
  local pprof_prefix="${RUN_DIR}/pprof/${label}"
  mkdir -p "${pprof_prefix}"
  # shellcheck disable=SC1091
  source "${venv}/bin/activate"
  local -a env_args=(
    "PORT=${port}"
    "DD_ENV=local-314v315-async"
    "DD_SERVICE=local-async-${side}"
    "DD_VERSION=$(git -C "${DDTRACE_SRC}" rev-parse --short HEAD)"
    "DD_PROFILING_ENABLED=${PROFILING_ENABLED}"
    "DD_TRACE_ENABLED=false"
  )
  if [[ "${PROFILING_ENABLED}" == "true" ]]; then
    env_args+=(
      "DD_PROFILING_LOCK_ENABLED=true"
      "DD_PROFILING_MEMORY_ENABLED=true"
      "DD_PROFILING_UPLOAD_INTERVAL=${UPLOAD_INTERVAL}"
      "DD_PROFILING_OUTPUT_PPROF=${pprof_prefix}/profile"
      "DD_PROFILING_TAGS=ab_side:${side},experiment:local_async_314v315,py:${label}"
    )
  fi
  env "${env_args[@]}" \
    python "${APP_PY}" \
    >"${RUN_DIR}/logs/server_${label}.log" 2>&1 &
  echo $! >"${RUN_DIR}/logs/server_${label}.pid"
  deactivate
  echo ">>> [${label}] pid=$(cat "${RUN_DIR}/logs/server_${label}.pid") port=${port} profiling=${PROFILING_ENABLED}"
}

_start "${RUN_DIR}/venvs/a314" "${PORT_A}" "A314" "A"
_start "${RUN_DIR}/venvs/b315" "${PORT_B}" "B315" "B"

cleanup() {
  if [[ -n "${METRICS_PID:-}" ]]; then
    kill "${METRICS_PID}" 2>/dev/null || true
  fi
  for label in A314 B315; do
    if [[ -f "${RUN_DIR}/logs/server_${label}.pid" ]]; then
      kill "$(cat "${RUN_DIR}/logs/server_${label}.pid")" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT

echo ">>> waiting for /healthz..."
for port in "${PORT_A}" "${PORT_B}"; do
  ok=0
  for _ in $(seq 1 90); do
    if curl -sf "http://127.0.0.1:${port}/healthz" >/dev/null; then
      ok=1
      break
    fi
    sleep 1
  done
  if [[ "${ok}" != 1 ]]; then
    echo "ERROR: port ${port} never became ready; logs:" >&2
    ls -la "${RUN_DIR}/logs/" >&2
    tail -80 "${RUN_DIR}/logs/"*.log >&2 || true
    exit 1
  fi
done
echo ">>> both sides up"

# Snapshot /stats mid-soak helper (written after drive too).
_snap_stats() {
  local tag="$1"
  for port_label in "A314:${PORT_A}" "B315:${PORT_B}"; do
    local label="${port_label%%:*}"
    local port="${port_label##*:}"
    curl -sf "http://127.0.0.1:${port}/stats" \
      >"${RUN_DIR}/logs/stats_${tag}_${label}.json" 2>/dev/null \
      || echo '{"error":"stats_fetch_failed"}' >"${RUN_DIR}/logs/stats_${tag}_${label}.json"
  done
}
_snap_stats "pre"

METRICS_CSV="${RUN_DIR}/logs/proc_metrics.csv"
echo "t_s,label,pid,pcpu,rss_kb" >"${METRICS_CSV}"
(
  t0="$(date +%s)"
  while true; do
    now="$(date +%s)"
    t_s="$((now - t0))"
    for label in A314 B315; do
      pid_file="${RUN_DIR}/logs/server_${label}.pid"
      [[ -f "${pid_file}" ]] || continue
      pid="$(cat "${pid_file}")"
      line="$(ps -p "${pid}" -o %cpu=,rss= 2>/dev/null | awk '{print $1","$2}')"
      if [[ -n "${line}" ]]; then
        echo "${t_s}.0,${label},${pid},${line}" >>"${METRICS_CSV}"
      fi
    done
    sleep 1
  done
) &
METRICS_PID=$!

echo ">>> drive ${DURATION}s concurrency=${CONCURRENCY}"
python3 "${DRIVE_PY}" \
  --auth "Bearer unused" \
  --sides "A=${PORT_A},B=${PORT_B}" \
  --requests-file "${CORPUS}" \
  --concurrency "${CONCURRENCY}" \
  --duration "${DURATION}" \
  --shuffle-seed 1337 \
  --stats-out "${RUN_DIR}/logs/drive_stats.json" \
  2>&1 | tee "${RUN_DIR}/logs/drive.log"
touch "${RUN_DIR}/logs/drive_done.flag"

_snap_stats "post"

kill "${METRICS_PID}" 2>/dev/null || true
METRICS_PID=""

if [[ "${PROFILING_ENABLED}" == "true" ]]; then
  sleep $((UPLOAD_INTERVAL + 5))
else
  sleep 2
fi

echo "=== summary + delta table ==="
python3 - <<'PY' "${RUN_DIR}" "${DDTRACE_SRC}" "${DURATION}" "${PORT_A}" "${PORT_B}" "${PY314_BIN}" "${PY315_BIN}" "${PROFILING_ENABLED}"
from __future__ import annotations

import json
import pathlib
import re
import statistics
import subprocess
import sys
from typing import Any

run: pathlib.Path = pathlib.Path(sys.argv[1])
ddtrace_src: str = sys.argv[2]
duration_s: int = int(sys.argv[3])
port_a: int = int(sys.argv[4])
port_b: int = int(sys.argv[5])
py314: str = sys.argv[6]
py315: str = sys.argv[7]
profiling_enabled: bool = sys.argv[8].lower() in ("1", "true", "yes", "on")

ddtrace_sha: str = subprocess.check_output(
    ["git", "-C", ddtrace_src, "rev-parse", "HEAD"], text=True
).strip()

drive_stats: dict[str, Any] = {}
drive_path: pathlib.Path = run / "logs" / "drive_stats.json"
if drive_path.exists():
    drive_stats = json.loads(drive_path.read_text())

if not drive_stats:
    drive_log: str = (run / "logs" / "drive.log").read_text(errors="replace")
    for m in re.finditer(r"^(A|B): total=(\d+) ok=(\d+) err=(\d+)", drive_log, re.M):
        drive_stats[m.group(1)] = {
            "total": int(m.group(2)),
            "ok": int(m.group(3)),
            "err": int(m.group(4)),
            "codes": {},
        }


def pctile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    ys: list[float] = sorted(xs)
    idx: int = min(len(ys) - 1, max(0, int(round((p / 100.0) * (len(ys) - 1)))))
    return ys[idx]


def load_proc(label: str) -> dict[str, Any]:
    csv_path: pathlib.Path = run / "logs" / "proc_metrics.csv"
    cpus: list[float] = []
    rss_mibs: list[float] = []
    if csv_path.exists():
        for ln in csv_path.read_text().splitlines()[1:]:
            parts: list[str] = ln.split(",")
            if len(parts) < 5 or parts[1] != label:
                continue
            cpus.append(float(parts[3]))
            rss_mibs.append(float(parts[4]) / 1024.0)
    return {
        "n": len(cpus),
        "cpu_mean": statistics.fmean(cpus) if cpus else 0.0,
        "cpu_p95": pctile(cpus, 95),
        "cpu_max": max(cpus) if cpus else 0.0,
        "rss_mean_mb": statistics.fmean(rss_mibs) if rss_mibs else 0.0,
        "rss_p95_mb": pctile(rss_mibs, 95),
        "rss_max_mb": max(rss_mibs) if rss_mibs else 0.0,
    }


def load_http_stats(label: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tag in ("pre", "post"):
        p: pathlib.Path = run / "logs" / f"stats_{tag}_{label}.json"
        if p.exists():
            try:
                out[tag] = json.loads(p.read_text())
            except Exception:
                out[tag] = {"error": "parse_failed"}
    return out


def pprof_sample_types(label: str) -> list[str]:
    """Best-effort sample-type names via zstd + go tool pprof -raw."""
    import tempfile

    pdir: pathlib.Path = run / "pprof" / label
    if not pdir.exists():
        return []
    pprofs: list[pathlib.Path] = sorted(pdir.glob("profile*.pprof"))
    if not pprofs:
        return []
    sample: pathlib.Path = pprofs[min(2, len(pprofs) - 1)]  # mid-ish window
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pb", delete=False) as tmp:
            tmp_path = tmp.name
        # Local OUTPUT_PPROF artifacts are zstd-compressed.
        subprocess.check_call(
            ["zstd", "-d", "-f", "-o", tmp_path, str(sample)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        raw: str = subprocess.check_output(
            ["go", "tool", "pprof", "-raw", tmp_path],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    except Exception:
        return []
    finally:
        if tmp_path is not None:
            pathlib.Path(tmp_path).unlink(missing_ok=True)
    types: list[str] = []
    for ln in raw.splitlines():
        if not ln.startswith("cpu-time/") and "cpu-time/" not in ln:
            continue
        # Single line listing all types: cpu-time/... wall-time/... ...
        for tok in ln.split():
            name: str = tok.split("/")[0]
            if name and name not in types:
                types.append(name)
        break
    return types


def pprof_meta(label: str) -> dict[str, Any]:
    pdir: pathlib.Path = run / "pprof" / label
    pprofs: list[pathlib.Path] = sorted(pdir.glob("profile*.pprof")) if pdir.exists() else []
    metas: list[pathlib.Path] = sorted(pdir.glob("profile*.internal_metadata.json")) if pdir.exists() else []
    sample_cpu: int = 0
    sample_count: int = 0
    task_counts: list[float] = []
    task_min: float = 0.0
    task_max: float = 0.0
    for mp in metas:
        try:
            data: dict[str, Any] = json.loads(mp.read_text())
        except Exception:
            continue
        raw_cpu: object = data.get("sample_capture_cpu_time_us", data.get("sample_capture_cpu_us", 0))
        sample_cpu += int(raw_cpu or 0)
        sample_count += int(data.get("sample_count", 0) or 0)
        if "asyncio_task_count" in data:
            task_counts.append(float(data["asyncio_task_count"]))
        elif "task_count" in data:
            task_counts.append(float(data["task_count"]))
    if task_counts:
        task_min = min(task_counts)
        task_max = max(task_counts)
    all_bytes: int = sum(p.stat().st_size for p in pdir.iterdir()) if pdir.exists() else 0
    return {
        "pprof_count": len(pprofs),
        "pprof_bytes": all_bytes,
        "sample_capture_cpu_time_us": sample_cpu,
        "sample_count": sample_count,
        "asyncio_task_count_mean": statistics.fmean(task_counts) if task_counts else 0.0,
        "asyncio_task_count_min": task_min,
        "asyncio_task_count_max": task_max,
        "asyncio_task_count_n": len(task_counts),
        "sample_types": pprof_sample_types(label),
    }


summary: dict[str, Any] = {
    "run_dir": str(run),
    "workload": "async_long_lived_loop",
    "ddtrace_src": ddtrace_src,
    "ddtrace_sha": ddtrace_sha,
    "duration_s": duration_s,
    "profiling_enabled": profiling_enabled,
    "ports": {"A": port_a, "B": port_b},
    "sides": {},
}
for label, side, pybin in [("A314", "A", py314), ("B315", "B", py315)]:
    log: str = (run / "logs" / f"server_{label}.log").read_text(errors="replace")
    meta: dict[str, Any] = pprof_meta(label)
    proc: dict[str, Any] = load_proc(label)
    http_stats: dict[str, Any] = load_http_stats(label)
    post: dict[str, Any] = http_stats.get("post") or {}
    summary["sides"][side] = {
        "label": label,
        "python_bin": pybin,
        "profiler_started": "profiler started" in log,
        "server_log_tail": log.strip().splitlines()[-12:],
        "drive": drive_stats.get(side, {}),
        "proc": proc,
        "http_stats": http_stats,
        "http_asyncio_task_count_post": float(post.get("asyncio_task_count", 0) or 0),
        "http_named_tasks_n_post": int(post.get("named_tasks_n", 0) or 0),
        **meta,
    }

(out := run / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(out.read_text())


def fmt_delta(a: float, b: float, kind: str) -> tuple[str, str, str, str]:
    d: float = b - a
    if kind == "int":
        a_s, b_s, d_s = str(int(a)), str(int(b)), f"{d:+.0f}"
    elif kind == "float":
        a_s, b_s, d_s = f"{a:.2f}", f"{b:.2f}", f"{d:+.2f}"
    elif kind == "pct":
        a_s, b_s, d_s = f"{a:.1f}%", f"{b:.1f}%", f"{d:+.1f}%"
    elif kind == "mib":
        a_s, b_s, d_s = f"{a:.1f} MiB", f"{b:.1f} MiB", f"{d:+.1f} MiB"
    elif kind == "mb":
        a_s, b_s, d_s = f"{a / 1e6:.1f} MB", f"{b / 1e6:.1f} MB", f"{(d) / 1e6:+.1f} MB"
    elif kind == "err_pct":
        a_s, b_s, d_s = f"{a:.3f}%", f"{b:.3f}%", f"{d:+.3f}%"
    else:
        a_s, b_s, d_s = str(a), str(b), f"{d:+}"
    pct: str
    if a == 0:
        pct = "+inf%" if d != 0 else "+0.0%"
    else:
        pct = f"{(d / a) * 100:+.1f}%"
    return a_s, b_s, d_s, pct


sa: dict[str, Any] = summary["sides"]["A"]
sb: dict[str, Any] = summary["sides"]["B"]
da: dict[str, Any] = sa.get("drive") or {}
db: dict[str, Any] = sb.get("drive") or {}
pa: dict[str, Any] = sa["proc"]
pb: dict[str, Any] = sb["proc"]

rows_spec: list[tuple[str, float, float, str]] = [
    ("req total ({}s)".format(duration_s), float(da.get("total", 0)), float(db.get("total", 0)), "int"),
    ("req/s", float(da.get("total", 0)) / duration_s, float(db.get("total", 0)) / duration_s, "float"),
    ("errors", float(da.get("err", 0)), float(db.get("err", 0)), "int"),
    (
        "error rate",
        (100.0 * float(da.get("err", 0)) / float(da["total"])) if da.get("total") else 0.0,
        (100.0 * float(db.get("err", 0)) / float(db["total"])) if db.get("total") else 0.0,
        "err_pct",
    ),
    ("CPU% mean (ps 1Hz)", pa["cpu_mean"], pb["cpu_mean"], "pct"),
    ("CPU% p95", pa["cpu_p95"], pb["cpu_p95"], "pct"),
    ("CPU% max", pa["cpu_max"], pb["cpu_max"], "pct"),
    ("RSS mean", pa["rss_mean_mb"], pb["rss_mean_mb"], "mib"),
    ("RSS p95", pa["rss_p95_mb"], pb["rss_p95_mb"], "mib"),
    ("RSS max", pa["rss_max_mb"], pb["rss_max_mb"], "mib"),
    (
        "asyncio_task_count mean (pprof meta)",
        float(sa["asyncio_task_count_mean"]),
        float(sb["asyncio_task_count_mean"]),
        "float",
    ),
    (
        "asyncio_task_count max (pprof meta)",
        float(sa["asyncio_task_count_max"]),
        float(sb["asyncio_task_count_max"]),
        "float",
    ),
    (
        "http /stats task_count (post)",
        float(sa["http_asyncio_task_count_post"]),
        float(sb["http_asyncio_task_count_post"]),
        "float",
    ),
    (
        "http named_tasks_n (post)",
        float(sa["http_named_tasks_n_post"]),
        float(sb["http_named_tasks_n_post"]),
        "int",
    ),
    ("pprof .pprof files", float(sa["pprof_count"]), float(sb["pprof_count"]), "int"),
    ("pprof bytes (all artifacts)", float(sa["pprof_bytes"]), float(sb["pprof_bytes"]), "mb"),
    (
        "profiler sample_capture_cpu_time_us sum",
        float(sa["sample_capture_cpu_time_us"]),
        float(sb["sample_capture_cpu_time_us"]),
        "int",
    ),
    ("profiler sample_count sum", float(sa["sample_count"]), float(sb["sample_count"]), "int"),
]

table: list[dict[str, str]] = []
print(f"{'metric':<46} {'A':>14} {'B':>14} {'delta':>12} {'delta%':>10}")
for name, a, b, kind in rows_spec:
    a_s, b_s, d_s, pct = fmt_delta(a, b, kind)
    table.append({"metric": name, "A": a_s, "B": b_s, "delta": d_s, "delta_pct": pct})
    print(f"{name:<46} {a_s:>14} {b_s:>14} {d_s:>12} {pct:>10}")

print("sample_types A:", sa.get("sample_types") or "(unavailable)")
print("sample_types B:", sb.get("sample_types") or "(unavailable)")

delta: dict[str, Any] = {
    "tip": ddtrace_sha,
    "workload": "async_long_lived_loop",
    "duration_s": duration_s,
    "profiling_enabled": profiling_enabled,
    "metrics_run": str(run),
    "table": table,
    "sample_types": {"A314": sa.get("sample_types"), "B315": sb.get("sample_types")},
    "proc_raw": {"A314": pa, "B315": pb},
    "caveat": (
        f"single {duration_s}s laptop asyncio soak, concurrency per side; "
        f"profiling_enabled={profiling_enabled}; "
        "CPU% from ps 1Hz; RSS process RSS; long-lived event loop (not per-request asyncio.run)"
    ),
}
(run / "delta_table.json").write_text(json.dumps(delta, indent=2) + "\n")

# Functional gate: elevated asyncio_task_count vs smoke's flat ~3.
ELEVATED_MIN: float = 10.0
if profiling_enabled:
    ok: bool = all(
        s["profiler_started"] and s["pprof_count"] > 0 for s in summary["sides"].values()
    )
    elevated: bool = all(
        float(s["asyncio_task_count_mean"]) >= ELEVATED_MIN for s in summary["sides"].values()
    )
    if ok and not elevated:
        print(
            f"WARN: profiler ok but asyncio_task_count mean < {ELEVATED_MIN} "
            f"(A={sa['asyncio_task_count_mean']:.1f} B={sb['asyncio_task_count_mean']:.1f}) — "
            "weak asyncio soak",
            flush=True,
        )
        # Still exit 0 if profiler wrote profiles; RESULTS will call out elevation.
    sys.exit(0 if ok else 2)
else:
    ok = all(
        (not s["profiler_started"]) and int((s.get("drive") or {}).get("ok", 0) or 0) > 0
        for s in summary["sides"].values()
    )
    sys.exit(0 if ok else 2)
PY

echo "DONE RUN_DIR=${RUN_DIR}"
