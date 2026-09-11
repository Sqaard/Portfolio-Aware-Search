"""In-container half of the live-arrival benchmark (``--ss-storage native``).

When the inbox, checkpoint and state live on the container's own filesystem, the
producer must run next to Spark too -- the arrangement of a crawler deployed with
the stream processor. This script runs the real producer
(``crawler.live_incremental_fetch --simulate-from``) inside the container, then
waits for ``state.json`` to show the batch merged, and prints both timestamps. Both
come from the container clock, so the latency never mixes the Windows and WSL
clocks.

    python3 deploy/spark_cluster/live_arrival_probe.py --corpus C --size 12 --seed 1001 \\
        --inbox /tmp/live/inbox --live-file /tmp/live/processed.jsonl \\
        --state /tmp/live/state/state.json --target 24
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--live-file", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--target", type=int, required=True, help="total_documents that marks the batch merged")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    # The simulate branch of live_incremental_fetch IS simulate_live_fetch; calling
    # it directly avoids importing the live crawlers (requests, which the Spark
    # image does not ship).
    from crawler.streaming_delivery import simulate_live_fetch

    delivered = simulate_live_fetch(
        corpus=Path(args.corpus), count=args.size, seed=args.seed,
        processed_output=Path(args.live_file), streaming_inbox=Path(args.inbox),
    )

    state_path = Path(args.state)
    deadline = time.time() + args.timeout
    while True:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if int(state.get("total_documents", 0) or 0) >= args.target:
            done = time.time()
            break
        if time.time() > deadline:
            raise SystemExit(f"timed out waiting for {args.target} documents")
        time.sleep(0.01)
    print(json.dumps({"delivered": delivered, "done": done,
                      "last_batch": (state.get("batches") or [{}])[-1],
                      "last_batch_id": state.get("last_batch_id")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
