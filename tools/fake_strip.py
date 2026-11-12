#!/usr/bin/env python3
"""A stand-in for the strip, to try scenes without touching the real lamp.

    fake_strip.py                 # listens on 127.0.0.1:8766
    scenes.py --host 127.0.0.1:8766 temporale film --seconds 20

Answers the calls the tools make (/api/state, /api/raw, /api/set, /api/flash,
/api/scene) and counts them: GET /fake shows the counters and the last colour.
/fake?wake=sunrise pretends a wake-up started, to see scenes give way.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

PORT = 8766
state = {"on": True, "h": 35.0, "s": 50.0, "v": 100, "wake": "", "wake_phase": "", "scenes": "", "fw": "fake"}
seen = {"raw": 0, "set": 0, "flash": 0, "refused": 0, "last_raw": None, "last_set": None, "last_flash": None}


class Strip(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        url = urlsplit(self.path)
        query = dict(parse_qsl(url.query))
        if url.path == "/api/raw":
            if state["wake_phase"] not in ("", "waiting"):
                seen["refused"] += 1
                return self.reply(409, "wake-up running: the lamp is taken")
            seen["raw"] += 1
            seen["last_raw"] = [int(query.get(channel, 0)) for channel in "rgb"]
        elif url.path == "/api/set":
            seen["set"] += 1
            seen["last_set"] = query
            state.update({key: (value != "0" if key == "on" else float(value)) for key, value in query.items() if key in state})
        elif url.path == "/api/flash":
            seen["flash"] += 1
            seen["last_flash"] = query
            return self.reply(200, "ok")
        elif url.path == "/api/scene":
            on = set(filter(None, state["scenes"].split(","))) - {query.get("name", "")}
            state["scenes"] = ",".join(sorted(on | ({query["name"]} if query.get("on") != "0" else set())))
        elif url.path == "/fake":
            if "wake" in query:
                state["wake_phase"] = query["wake"]
            return self.reply(200, json.dumps(seen))
        elif url.path != "/api/state":
            return self.reply(404, "not found")
        self.reply(200, json.dumps(state))

    def reply(self, code: int, text: str) -> None:
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:   # quiet: scenes send fifteen frames a second
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    print(f"striscia finta su http://127.0.0.1:{port} (Ctrl-C per fermare)")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Strip).serve_forever()
    except KeyboardInterrupt:
        pass
