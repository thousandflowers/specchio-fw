#!/usr/bin/env python3
"""Measure how responsive the strip is, to compare firmwares and Wi-Fi settings.

    bench.py <strip-address> --path /get_status --post i=9999   # stock Athom firmware
    bench.py specchio.local                                    # this firmware

Reports ping loss/latency and HTTP latency. Pings slower than SLOW_PING_MS
are counted apart: on a LAN they mean the radio was asleep or retransmitting.
"""
import argparse
import re
import statistics
import subprocess
import time
import urllib.error
import urllib.request

SLOW_PING_MS = 50
PING_INTERVAL_S = 0.25
HTTP_TIMEOUT_S = 10
HTTP_PAUSE_S = 0.3


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def ping(host: str, count: int) -> tuple[list[float], int]:
    result = subprocess.run(
        ["ping", "-c", str(count), "-i", str(PING_INTERVAL_S), host],
        capture_output=True, text=True, timeout=count * 3 + 30)
    times = [float(t) for t in re.findall(r"time=([\d.]+)", result.stdout)]
    return times, count - len(times)


def http(host: str, path: str, post: str, count: int) -> tuple[list[float], int]:
    url = f"http://{host}{path}"
    data = post.encode() if post else None
    times, failures = [], 0
    for _ in range(count):
        start = time.monotonic()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=HTTP_TIMEOUT_S) as reply:
                reply.read()
            times.append((time.monotonic() - start) * 1000)
        except (urllib.error.URLError, OSError):
            failures += 1
        time.sleep(HTTP_PAUSE_S)
    return times, failures


def describe(name: str, times: list[float], failures: int, total: int) -> str:
    if not times:
        return f"{name}: all {total} failed"
    return (f"{name}: n={total} lost={failures} ({failures / total:.0%})  "
            f"median={statistics.median(times):.0f}ms  p95={percentile(times, 0.95):.0f}ms  "
            f"max={max(times):.0f}ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host")
    parser.add_argument("--path", default="/api/state")
    parser.add_argument("--post", default="", help="send this body as a POST instead of a GET")
    parser.add_argument("--pings", type=int, default=200)
    parser.add_argument("--requests", type=int, default=40)
    args = parser.parse_args()

    ping_times, ping_lost = ping(args.host, args.pings)
    print(describe("ping", ping_times, ping_lost, args.pings))
    if ping_times:
        slow = sum(t > SLOW_PING_MS for t in ping_times)
        print(f"ping slower than {SLOW_PING_MS}ms: {slow}/{len(ping_times)} ({slow / len(ping_times):.0%})")
    http_times, http_failed = http(args.host, args.path, args.post, args.requests)
    print(describe("http", http_times, http_failed, args.requests))


if __name__ == "__main__":
    main()
