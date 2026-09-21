#!/usr/bin/env python3
"""Byte-identical N-side GET load driver for local smoke A/B.

Every configured side replays the same corpus in the same order. GET only;
response bodies are drained and discarded (status codes only).
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
from typing import Any
import urllib.error
import urllib.request


def load_corpus(requests_file: str, shuffle_seed: int) -> list[str]:
    with open(requests_file) as f:
        paths: list[str] = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    if not paths:
        raise SystemExit(f"empty corpus: {requests_file}")
    bad: list[str] = [p for p in paths if not p.startswith("/")]
    if bad:
        raise SystemExit(f"corpus paths must start with '/': {bad[:3]}")
    rng: random.Random = random.Random(shuffle_seed)  # nosec B311 — deterministic corpus shuffle
    rng.shuffle(paths)
    print(f"corpus: {len(paths)} GET paths [{requests_file}], shuffle_seed={shuffle_seed}", flush=True)
    return paths


def worker(
    base_url: str,
    headers: dict[str, str],
    corpus: list[str],
    start_idx: int,
    stride: int,
    deadline: float,
    think_s: float,
    stats: dict[str, Any],
) -> None:
    i: int = start_idx
    n: int = len(corpus)
    while time.time() < deadline:
        url: str = base_url + corpus[i % n]
        i += stride
        code: int
        try:
            req: urllib.request.Request = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=60) as r:  # nosec B310 — localhost GET only
                r.read()
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception:
            code = -1
        with stats["lock"]:
            stats["total"] += 1
            if 200 <= code < 300:
                stats["ok"] += 1
            else:
                stats["err"] += 1
                stats["codes"][code] = stats["codes"].get(code, 0) + 1
        if code == -1:
            time.sleep(0.5)
        elif think_s > 0:
            time.sleep(think_s)


def parse_sides(spec: str) -> dict[str, str]:
    sides: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SystemExit(f"--sides entry must be NAME=PORT: '{part}'")
        name: str
        port: str
        name, port = part.split("=", 1)
        name, port = name.strip(), port.strip()
        if not name or not port.isdigit():
            raise SystemExit(f"--sides entry must be NAME=PORT (port numeric): '{part}'")
        sides[name] = f"http://127.0.0.1:{port}"
    if not sides:
        raise SystemExit("--sides resolved to no sides")
    return sides


def main() -> None:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--auth", default="Bearer unused", help="Authorization header value")
    ap.add_argument("--sides", default="A=18400,B=18401", help="Comma-separated NAME=PORT")
    ap.add_argument("--concurrency", type=int, default=2, help="Worker threads per side")
    ap.add_argument("--duration", type=int, default=90, help="Seconds to drive")
    ap.add_argument("--think-ms", type=int, default=0, help="Per-worker sleep between requests, ms")
    ap.add_argument("--shuffle-seed", type=int, default=1337, help="Deterministic corpus order seed")
    ap.add_argument("--requests-file", required=True, help="File of GET paths (one per line)")
    ap.add_argument("--stats-out", default=None, help="Optional JSON path for final per-side stats")
    args: argparse.Namespace = ap.parse_args()

    sides: dict[str, str] = parse_sides(args.sides)
    corpus: list[str] = load_corpus(args.requests_file, args.shuffle_seed)
    think_s: float = max(0, args.think_ms) / 1000.0
    headers: dict[str, str] = {"Authorization": args.auth, "Accept": "application/json"}
    deadline: float = time.time() + args.duration

    print(
        f"sides: {', '.join(f'{s}->{u}' for s, u in sides.items())}  "
        f"concurrency={args.concurrency}/side  duration={args.duration}s  "
        f"think={args.think_ms}ms",
        flush=True,
    )

    stats: dict[str, dict[str, Any]] = {
        s: {"lock": threading.Lock(), "total": 0, "ok": 0, "err": 0, "codes": {}} for s in sides
    }
    threads: list[threading.Thread] = []
    for side, base_url in sides.items():
        for w in range(args.concurrency):
            t: threading.Thread = threading.Thread(
                target=worker,
                args=(base_url, headers, corpus, w, args.concurrency, deadline, think_s, stats[side]),
                daemon=True,
            )
            t.start()
            threads.append(t)

    start: float = time.time()
    while time.time() < deadline:
        time.sleep(15)
        el: int = int(time.time() - start)
        parts: list[str] = []
        for s in sides:
            st: dict[str, Any] = stats[s]
            with st["lock"]:
                parts.append(f"{s}: total={st['total']} ok={st['ok']} err={st['err']} codes={st['codes']}")
        print(f"[t+{el}s] " + " | ".join(parts), flush=True)

    for t in threads:
        t.join(timeout=65)
    print("=== FINAL ===", flush=True)
    final: dict[str, Any] = {}
    for s in sides:
        st = stats[s]
        with st["lock"]:
            final[s] = {"total": st["total"], "ok": st["ok"], "err": st["err"], "codes": dict(st["codes"])}
        print(
            f"{s}: total={st['total']} ok={st['ok']} err={st['err']} codes={st['codes']}",
            flush=True,
        )
    if args.stats_out:
        with open(args.stats_out, "w") as out:
            json.dump(final, out, indent=2)
            out.write("\n")


if __name__ == "__main__":
    main()
