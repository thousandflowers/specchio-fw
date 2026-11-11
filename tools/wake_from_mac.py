#!/usr/bin/env python3
"""Wake-up light driven from the Mac, for firmware without /api/wake.

Sunrise for --lead minutes before the alarm, lamp left on at the alarm, then
after --insist minutes on/off blinks that get faster for --insist-for minutes.
Stops as soon as the lamp is changed by hand (button, Home app, web page).

The alarm times come from the iPhone, one Shortcuts automation per source:
  /wake?at=07:00,07:30&src=orologio   every alarm that source has (replaces its list);
                                      a POST body works too, any text with times in it
  /ring?src=sleepcycle                an app that only says "ringing now": full light, no sunrise
  /stop                               the alarm was stopped on the phone
The earliest alarm among all sources is the one that runs.

  caffeinate -is python3 tools/wake_from_mac.py --listen 8737
  python3 tools/wake_from_mac.py --at 07:30        (one alarm, no phone)
  python3 tools/wake_from_mac.py --selftest

ponytail: the Mac must stay awake and open all night, and anyone on the home
network can set the alarm; the real fix is the alarm inside the strip
(/api/wake, same URL shape), blocked until 2.4 can join the network.
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import threading
import urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple, Optional
from urllib.parse import parse_qs, urlparse

SLOWEST_BLINK_S = 12.0  # gap between blinks when the insisting starts
FASTEST_BLINK_S = 1.5   # and when it ends: the link is too weak for more
OFF_S = 0.6             # how long each blink stays dark
RETRIES = 5
# Alarms survive a restart of the Mac or of this program.
SAVED = Path.home() / "Library/Application Support/specchio-wake/alarms.json"


class Plan(NamedTuple):
    host: str
    lead_min: float
    insist_min: float
    insist_for_min: float


def say(msg: str) -> None:
    print(dt.datetime.now().strftime("%H:%M:%S"), msg, flush=True)


def call(host: str, path: str) -> Optional[str]:
    """GET with retries: at -83 dBm single requests do get lost."""
    last: Optional[OSError] = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(f"http://{host}{path}", timeout=4) as reply:
                return reply.read().decode()
        except OSError as err:
            last = err
            threading.Event().wait(1 + attempt)
    say(f"no answer for {path}: {last}")
    return None


def lamp(host: str) -> Optional[dict]:
    text = call(host, "/api/state")
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def was_touched(state: Optional[dict], held: Optional[str]) -> bool:
    """True when someone switched the lamp off or changed its colour."""
    return bool(state) and (not state.get("on") or state.get("hex") != held)


def blink_gap(progress: float) -> float:
    """Seconds between blinks, progress 0..1 through the insisting phase."""
    progress = min(max(progress, 0.0), 1.0)
    return SLOWEST_BLINK_S + (FASTEST_BLINK_S - SLOWEST_BLINK_S) * progress


# A clock anywhere in a text: 07:30, 7.30, 7:30 PM. The phone sends its alarms
# however Shortcuts prints them, so the times are fished out, not expected in a format.
# In a timestamp (2026-11-12T07:30:00+01:00) the seconds and the zone offset are not clocks.
CLOCK = re.compile(r"(?<![\d:+\-])([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)(?:[\s\u202f]*([AaPp])\.?[Mm]\b)?")


def find_clocks(text: str) -> list:
    """Every time of day in the text, as 24-hour HH:MM strings, in order."""
    found = []
    for hour, minute, half in CLOCK.findall(text):
        h = int(hour)
        if half:
            if h < 1 or h > 12:
                continue  # 13:00 PM is not a time
            h = h % 12 + (12 if half.lower() == "p" else 0)
        found.append(f"{h:02d}:{minute}")
    return found


def next_alarm(clock: str, now: dt.datetime) -> dt.datetime:
    hour, minute = map(int, clock.replace(".", ":").split(":"))
    alarm = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return alarm if alarm > now else alarm + dt.timedelta(days=1)


def wait_until(moment: dt.datetime, stop: threading.Event) -> bool:
    """False when cancelled. Short naps: a long sleep overshoots if the Mac dozed."""
    while (left := (moment - dt.datetime.now()).total_seconds()) > 0:
        if stop.wait(min(left, 30)):
            return False
    return not stop.is_set()


def run(plan: Plan, alarm: dt.datetime, stop: threading.Event) -> None:
    host, lead_min = plan.host, plan.lead_min
    start = alarm - dt.timedelta(minutes=lead_min)
    if start < dt.datetime.now():  # alarm closer than the lead: shorter sunrise
        start = dt.datetime.now()
        lead_min = max((alarm - start).total_seconds() / 60, 0.1)
    say(f"alarm {alarm:%d/%m %H:%M}, sunrise from {start:%H:%M:%S}, blinks after {plan.insist_min} min")
    if not wait_until(start, stop):
        return say("cancelled before the sunrise")
    say("sunrise" if call(host, f"/api/effect?name=alba&minutes={lead_min:.1f}") else "SUNRISE NOT STARTED")

    if not wait_until(alarm + dt.timedelta(seconds=5), stop):
        return say("cancelled during the sunrise, lamp left as it is")
    state = lamp(host)
    if not state or not state.get("on"):  # sunrise lost or stopped by hand: full light anyway
        call(host, "/api/set?hex=fff0d0&v=100")
        state = lamp(host)
    held = state.get("hex") if state else None
    say(f"alarm time, lamp on ({held})")

    insist_from = alarm + dt.timedelta(minutes=plan.insist_min)
    while dt.datetime.now() < insist_from:
        if stop.wait(10):
            return say("alarm stopped on the phone")
        if was_touched(lamp(host), held):
            return say("lamp changed by hand: you are up, stopping")

    say("still untouched: blinking")
    end = insist_from + dt.timedelta(minutes=plan.insist_for_min)
    while (now := dt.datetime.now()) < end and not stop.is_set():
        if was_touched(lamp(host), held):
            return say("lamp changed by hand: you are up, stopping")
        call(host, "/api/set?on=0")
        stop.wait(OFF_S)
        call(host, "/api/set?on=1")
        stop.wait(blink_gap((now - insist_from) / (end - insist_from)))
    call(host, "/api/set?on=1")
    say("alarm stopped on the phone, lamp left on" if stop.is_set() else "done, lamp left on")


def earliest_after(sources: dict, now: dt.datetime) -> Optional[dt.datetime]:
    """Next alarm among every source (Clock, Sleep, any app that can tell us)."""
    return min((a for alarms in sources.values() for a in alarms if a > now), default=None)


class Schedule:
    """Every alarm the phone told us about, by source; one wake-up runs at a time."""

    def __init__(self, plan: Plan) -> None:
        self.plan = plan
        self.sources: dict = {}  # source name -> list of alarm datetimes
        self.current: Optional[dt.datetime] = None
        self.stop = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.awake: Optional[subprocess.Popen] = None  # the caffeinate holding the Mac awake
        self.lock = threading.RLock()
        try:
            saved = json.loads(SAVED.read_text())
            self.sources = {n: [dt.datetime.fromisoformat(a) for a in alarms] for n, alarms in saved.items()}
        except (OSError, ValueError, AttributeError, TypeError):
            pass  # nothing saved yet, or unreadable: start empty
        self._arm()

    def _save(self) -> None:
        try:
            SAVED.parent.mkdir(parents=True, exist_ok=True)
            SAVED.write_text(json.dumps({n: [a.isoformat() for a in alarms] for n, alarms in self.sources.items()}))
        except OSError as err:
            say(f"could not save the alarms: {err}")

    def set_source(self, name: str, alarms: list) -> None:
        with self.lock:
            self.sources = {**self.sources, name: alarms}
            self._save()
            self._arm()

    def ring_now(self) -> None:
        """An app that cannot tell the time in advance says "my alarm is ringing"."""
        with self.lock:
            self._start(dt.datetime.now())

    def stop_current(self) -> None:
        with self.lock:
            self.sources = {n: [a for a in alarms if a != self.current] for n, alarms in self.sources.items()}
            self._save()
            self._start(None)
            self._arm()

    def describe(self) -> str:
        with self.lock:
            lines = [f"{name}: " + (", ".join(f"{a:%d/%m %H:%M}" for a in alarms) or "-")
                     for name, alarms in self.sources.items()]
            lines.append(f"prossima: {self.current:%d/%m %H:%M}" if self.current else "nessuna sveglia")
            return "\n".join(lines)

    def _is_running(self) -> bool:
        return self.thread is not None and self.thread.is_alive() and self.thread is not threading.current_thread()

    def _arm(self) -> None:
        now = dt.datetime.now()
        if self._is_running() and self.current and self.current <= now:
            return  # ringing right now: a re-sync from the phone must not restart it
        upcoming = earliest_after(self.sources, now)
        if upcoming == self.current and self._is_running():
            return
        self._start(upcoming)

    def _start(self, alarm: Optional[dt.datetime]) -> None:
        self.stop.set()
        self.stop, self.current, self.thread = threading.Event(), alarm, None
        if self.awake:
            self.awake.terminate()
            self.awake = None
        if alarm:
            # Keep the Mac from dozing until this wake-up is over (a closed lid still wins).
            whole_min = self.plan.insist_min + self.plan.insist_for_min + 5
            seconds = (alarm - dt.datetime.now()).total_seconds() + whole_min * 60
            self.awake = subprocess.Popen(["caffeinate", "-i", "-t", str(int(max(seconds, 60)))])
            self.thread = threading.Thread(target=self._run, args=(alarm, self.stop), daemon=True)
            self.thread.start()

    def _run(self, alarm: dt.datetime, stop: threading.Event) -> None:
        run(self.plan, alarm, stop)
        with self.lock:
            if self.stop is stop:  # ended by itself, not replaced: on to the next alarm
                self.current = None
                self._arm()


def serve(plan: Plan, port: int) -> None:
    schedule = Schedule(plan)

    class Handler(BaseHTTPRequestHandler):
        def answer(self, code: int, text: str) -> None:
            body = (text + "\n").encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 (name fixed by http.server)
            self.handle_any(b"")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self.handle_any(self.rfile.read(min(length, 20000)))

        def handle_any(self, body: bytes) -> None:
            url = urlparse(self.path)
            query = parse_qs(url.query, keep_blank_values=True)
            source = query.get("src", ["telefono"])[0][:24]
            sent = " ".join(query.get("at", []) + [body.decode("utf-8", "replace")]).strip()
            if sent:
                say(f"{source} sent: {sent[:300]!r}")
            if url.path == "/wake":  # every alarm that source has: replaces its list, empty = none
                clocks = find_clocks(sent)
                if sent and not clocks:
                    return self.answer(400, "nessun orario HH:MM trovato in quello che hai mandato")
                now = dt.datetime.now()
                schedule.set_source(source, [next_alarm(clock, now) for clock in clocks])
            elif url.path == "/ring":
                say(f"{source}: ringing now")
                schedule.ring_now()
            elif url.path == "/stop":
                schedule.stop_current()
            self.answer(200, schedule.describe())

        def log_message(self, *_: object) -> None:  # our own lines are enough
            pass

    say(f"listening on port {port}: /wake?at=HH:MM,HH:MM&src=NAME  /ring?src=NAME  /stop")
    ThreadingHTTPServer(("", port), Handler).serve_forever()


def selftest() -> None:
    assert blink_gap(0) == SLOWEST_BLINK_S and blink_gap(1) == FASTEST_BLINK_S
    assert blink_gap(-1) == SLOWEST_BLINK_S and blink_gap(9) == FASTEST_BLINK_S
    assert blink_gap(0.25) > blink_gap(0.75)
    evening = dt.datetime(2026, 11, 11, 23, 0)
    assert next_alarm("07:30", evening) == dt.datetime(2026, 11, 12, 7, 30)
    assert next_alarm("7.30", evening) == dt.datetime(2026, 11, 12, 7, 30)
    assert next_alarm("23:30", evening) == dt.datetime(2026, 11, 11, 23, 30)
    for bad in ("", "sette", "25:00"):
        try:
            next_alarm(bad, evening)
            raise AssertionError(f"accepted {bad!r}")
        except ValueError:
            pass
    assert was_touched({"on": False, "hex": "aa"}, "aa") and was_touched({"on": True, "hex": "bb"}, "aa")
    assert not was_touched({"on": True, "hex": "aa"}, "aa") and not was_touched(None, "aa")
    sources = {"orologio": [evening + dt.timedelta(hours=9), evening + dt.timedelta(hours=8)],
               "sonno": [evening + dt.timedelta(hours=7)], "vuota": []}
    assert earliest_after(sources, evening) == evening + dt.timedelta(hours=7)
    assert earliest_after(sources, evening + dt.timedelta(hours=8)) == evening + dt.timedelta(hours=9)
    assert earliest_after(sources, evening + dt.timedelta(days=1)) is None
    assert find_clocks("07:30,06:45") == ["07:30", "06:45"]
    assert find_clocks("Sveglia 7.05\nLavoro 23:10") == ["07:05", "23:10"]
    assert find_clocks("7:30 AM, 12:15 AM, 12:00 PM, 9:45\u202fPM") == ["07:30", "00:15", "12:00", "21:45"]
    assert find_clocks("2026-11-12T07:30:00+01:00") == ["07:30"]
    assert find_clocks("niente, 25:00, 7:99, 123:45") == []
    cancelled = threading.Event()
    cancelled.set()
    assert not wait_until(evening + dt.timedelta(days=999), cancelled)
    print("selftest ok")


if __name__ == "__main__":
    args = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    args.add_argument("--listen", type=int, metavar="PORT", help="wait for the phone to send the alarm time")
    args.add_argument("--at", help="one alarm at HH:MM, without the phone")
    args.add_argument("--host", default="192.0.2.66")
    args.add_argument("--lead", type=float, default=30, help="sunrise minutes before the alarm")
    args.add_argument("--insist", type=float, default=10, help="minutes after the alarm before blinking")
    args.add_argument("--insist-for", type=float, default=20, help="minutes of blinking")
    args.add_argument("--selftest", action="store_true")
    opts = args.parse_args()
    plan = Plan(opts.host, opts.lead, opts.insist, opts.insist_for)
    if opts.selftest:
        selftest()
    elif opts.listen:
        serve(plan, opts.listen)
    elif opts.at:
        run(plan, next_alarm(opts.at, dt.datetime.now()), threading.Event())
    else:
        args.error("give --listen PORT or --at HH:MM")
