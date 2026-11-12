#!/usr/bin/env python3
"""Scenes for the mirror, driven live from the Mac: light on the strip, sound from the Mac.

    scenes.py                        # menu: pick a scene by number
    scenes.py temporale sopra        # thunderstorm; also: lontano, notte
    scenes.py terremoti              # live: one pulse for every earthquake in the world
    scenes.py terremoti --replay     # the last 24 hours, one after the other
    scenes.py posto kilauea          # the mirror takes the colour of a far-away webcam
    scenes.py posto --giro           # every place in places.json, plus a preview page
    scenes.py aerei --qui 48.86,2.35  # planes overhead: the flag colours of the country they fly to
    scenes.py suoni                  # real recordings, one at a time: the light follows the thunder
    scenes.py check                  # self-check, touches neither lamp nor network

Ctrl-C stops a scene; the lamp goes back to what it was showing.
"""
import argparse
import collections
import colorsys
import itertools
import json
import math
import random
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, NamedTuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import sound
from demo_effects import Effect, Hsv, play, send, wave

HOST = "192.0.2.66"
NET_TIMEOUT_S = 12
TRIES = 3

# ---------- the lamp ----------


def fetch(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "curl/8"}), timeout=NET_TIMEOUT_S) as reply:
        return reply.read()


@contextmanager
def borrowed(host: str) -> Iterator[None]:
    """Raw frames take the lamp over: afterwards give it back exactly as it was."""
    before = json.loads(fetch(f"http://{host}/api/state"))
    try:
        yield
    finally:
        sound.stop()
        query = urlencode({"on": int(before["on"]), "h": before["h"], "s": before["s"], "v": before["v"]})
        for _ in range(TRIES):
            try:
                fetch(f"http://{host}/api/set?{query}")
                break
            except OSError:
                time.sleep(1)
        else:
            print("lampada non ripristinata: accendila o spegnila da Casa")


def hold(host: str, colour: Hsv) -> bool:
    return any(send(host, colour) for _ in range(TRIES))


def as_hex(colour: Hsv) -> str:
    rgb = colorsys.hsv_to_rgb(colour.hue % 360 / 360, colour.saturation, colour.value)
    return "#" + "".join(f"{round(channel * 255):02x}" for channel in rgb)


# ---------- thunderstorm ----------

FLASH_DECAY_S = 0.12
FLASH_TAIL_S = 1.0
THUNDER_POOL = 4
MIN_GAP_S = 0.8
AFTERGLOW = 0.35        # how much of the flash brightness the rumble of the thunder keeps flickering
SOUND_LEAD_S = 0.25     # afplay needs about this long to start: thunder is launched this early to land on the flash


class Storm(NamedTuple):
    base: Hsv                      # the light between strikes
    mean_gap_s: float              # strikes come at random (Poisson): bursts and long pauses around this mean
    distance: tuple[float, float]  # 0 = overhead, 1 = far: dims the flash and softens the thunder
    rain: str                      # noise colour, see sound.COLOURS
    rain_volume: float


STORMS = {
    "lontano": Storm(Hsv(225, 0.55, 0.10), 9, (0.6, 1.0), "pink", 0.5),
    "sopra": Storm(Hsv(230, 0.45, 0.14), 5, (0.0, 0.35), "white", 0.7),
    "notte": Storm(Hsv(235, 0.70, 0.03), 20, (0.7, 1.0), "brown", 0.6),
}


class Strike(NamedTuple):
    at: float
    distance: float
    strokes: tuple[tuple[float, float], ...]  # (seconds after `at`, strength 0..1)


def plan_strikes(storm: Storm, seconds: float) -> tuple[Strike, ...]:
    strikes = []
    at = random.uniform(1.5, 4.0)
    while at < seconds - 1:
        offsets = itertools.accumulate(random.uniform(0.07, 0.22) for _ in range(random.randint(1, 4)))
        strokes = tuple((offset, random.uniform(0.5, 1.0)) for offset in offsets)
        strikes.append(Strike(at, random.uniform(*storm.distance), strokes))
        at += max(MIN_GAP_S, random.expovariate(1 / storm.mean_gap_s))
    return tuple(strikes)


def flash_level(strikes: tuple[Strike, ...], elapsed: float) -> float:
    level = 0.0
    for strike in strikes:
        reach = 1 - 0.7 * strike.distance
        for offset, strength in strike.strokes:
            since = elapsed - strike.at - offset
            if 0 <= since < FLASH_TAIL_S:
                level += reach * strength * math.exp(-since / FLASH_DECAY_S)
    return min(1.0, level)


def rumble_level(strikes: tuple[Strike, ...], rumbles: tuple[tuple[float, ...], ...], elapsed: float) -> float:
    """The light keeps trembling with each thunder: its loudness envelope, sampled at sound.ENVELOPE_STEP_S."""
    level = 0.0
    for strike, rumble in zip(strikes, rumbles):
        index = int((elapsed - strike.at) / sound.ENVELOPE_STEP_S)
        if elapsed >= strike.at and index < len(rumble):
            level += AFTERGLOW * (1 - 0.7 * strike.distance) * rumble[index]
    return level


def storm_frame(storm: Storm, strikes: tuple[Strike, ...],
                rumbles: tuple[tuple[float, ...], ...] = ()) -> Callable[[float, float], Hsv]:
    def frame(elapsed: float, _total: float) -> Hsv:
        level = min(1.0, flash_level(strikes, elapsed) + rumble_level(strikes, rumbles, elapsed))
        glow = storm.base.value * (0.8 + 0.4 * wave(elapsed, 9.0))
        return Hsv(storm.base.hue, storm.base.saturation * (1 - level), glow + (1 - glow) * level)
    return frame


class Thunder(NamedTuple):
    distance: float
    path: Path
    envelope: tuple[float, ...]


def make_thunders(storm: Storm) -> tuple[Thunder, ...]:
    low, high = storm.distance
    distances = [low + (high - low) * index / (THUNDER_POOL - 1) for index in range(THUNDER_POOL)]
    waves = [(distance, sound.thunder(distance)) for distance in distances]
    return tuple(Thunder(distance, sound.save(samples, f"thunder-{index}"), sound.envelope(samples))
                 for index, (distance, samples) in enumerate(waves))


def run_storm(host: str, name: str, seconds: float, is_silent: bool, lead_s: float = SOUND_LEAD_S) -> None:
    storm = STORMS[name]
    strikes = plan_strikes(storm, seconds)
    chosen: tuple[Thunder, ...] = ()
    if not is_silent:
        print("preparo il suono…")
        thunders = make_thunders(storm)
        chosen = tuple(min(thunders, key=lambda thunder: abs(thunder.distance - strike.distance)) for strike in strikes)
    with borrowed(host):
        if not is_silent:
            sound.start()
            sound.loop(storm.rain, storm.rain_volume)
            for strike, thunder in zip(strikes, chosen):
                sound.later(max(0.0, strike.at - lead_s), thunder.path, 1 - 0.6 * strike.distance)
        frame = storm_frame(storm, strikes, tuple(thunder.envelope for thunder in chosen))
        sent, lost = play(host, Effect(name, name, seconds, frame), False)
    print(f"temporale {name}: {len(strikes)} fulmini, {sent / seconds:.0f} fotogrammi/s, {lost} persi")


# ---------- real recordings: the light follows the thunder in the sound ----------

SOUNDS_FILE = Path(__file__).with_name("sounds.json")
STORM_LIGHT = Hsv(228, 0.55, 0.08)   # the light while no thunder is sounding
LISTEN_S = 40
FLASH_KICK = 2.5        # a sudden rise in loudness adds this much extra light: the crack looks like a flash
NET_LAG_S = 0.06        # a frame needs about this long to reach the strip
STEADY_KINDS = frozenset({"pioggia"})   # rain has low thumps too, but nothing the light should flash for


class Sound(NamedTuple):
    key: str
    name: str
    kind: str
    author: str
    licence: str
    url: str


def load_sounds() -> tuple[Sound, ...]:
    return tuple(Sound(**entry) for entry in json.loads(SOUNDS_FILE.read_text()))


def thunder_frame(shape: tuple[float, ...], sound_started_s: float) -> Callable[[float, float], Hsv]:
    """Brightness follows the thunder's loudness; sound_started_s = when the sound becomes audible on the frame clock."""
    def frame(elapsed: float, _total: float) -> Hsv:
        index = int((elapsed - sound_started_s + NET_LAG_S) / sound.ENVELOPE_STEP_S)
        if not 0 <= index < len(shape):
            return STORM_LIGHT
        rise = max(0.0, shape[index] - shape[max(0, index - 2)])
        level = min(1.0, shape[index] + FLASH_KICK * rise)
        return Hsv(STORM_LIGHT.hue, STORM_LIGHT.saturation * (1 - level), STORM_LIGHT.value + (1 - STORM_LIGHT.value) * level)
    return frame


def listen(host: str, entry: Sound, seconds: float, lead_s: float) -> None:
    print(f"{entry.name}: scarico e preparo (solo la prima volta)…")
    source = sound.recording(entry.url)
    seconds = min(seconds, source.seconds)
    start = sound.busiest(source.thunder, seconds)
    clip = sound.excerpt(source, start, seconds) if source.seconds > seconds else source.path
    shape = () if entry.kind in STEADY_KINDS else source.thunder[int(start / sound.ENVELOPE_STEP_S):]
    print(f"{entry.name}, di {entry.author} ({entry.licence}): {seconds:.0f} s da {start:.0f} s")
    with borrowed(host):
        sound.start()
        sound.play(clip, 1.0, seconds)
        play(host, Effect(entry.key, entry.name, seconds, thunder_frame(shape, lead_s)), False)


def sounds_menu(host: str, seconds: float, lead_s: float) -> None:
    entries = load_sounds()
    while True:
        print()
        for number, entry in enumerate(entries, 1):
            print(f"{number:2}  {entry.kind:10} {entry.name}")
        answer = input("numero (Ctrl-C ferma, q esce): ").strip()
        if answer == "q":
            return
        if answer.isdigit() and 1 <= int(answer) <= len(entries):
            try:
                listen(host, entries[int(answer) - 1], seconds, lead_s)
            except KeyboardInterrupt:
                print("\nfermato")
            except (OSError, subprocess.CalledProcessError) as error:
                print(f"non riuscito: {error}")


# ---------- earthquakes ----------

QUAKE_FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
QUAKE_POLL_S = 60
QUAKE_BASE_S = 1.5       # a pulse lasts this plus one second per magnitude point
FULL_MAGNITUDE = 7.0     # from here up the pulse is at full brightness
REPLAY_SPEED = 3.0


class Quake(NamedTuple):
    key: str
    at: float          # epoch seconds
    magnitude: float
    place: str
    longitude: float


def parse_quakes(feed: dict) -> tuple[Quake, ...]:
    quakes = (
        Quake(item["id"], item["properties"]["time"] / 1000, item["properties"]["mag"] or 0.0,
              item["properties"]["place"] or "?", item["geometry"]["coordinates"][0])
        for item in feed["features"]
    )
    return tuple(sorted(quakes, key=lambda quake: quake.at))


def quake_pulse(quake: Quake, speed: float = 1.0) -> Effect:
    """Where on the planet picks the hue (longitude round the colour wheel), how strong picks light and length."""
    peak = min(1.0, quake.magnitude / FULL_MAGNITUDE)
    hue = (quake.longitude + 180) % 360

    def frame(elapsed: float, total: float) -> Hsv:
        envelope = min(1.0, elapsed / 0.3) * math.exp(-3 * elapsed / total)
        return Hsv(hue, 0.9, 0.02 + 0.98 * peak * envelope * random.uniform(0.55, 1.0))

    return Effect(quake.key, quake.place, (QUAKE_BASE_S + quake.magnitude) / speed, frame)


def show_quake(host: str, quake: Quake, speed: float, is_silent: bool) -> None:
    pulse = quake_pulse(quake, speed)
    minutes = (time.time() - quake.at) / 60
    print(f"M{quake.magnitude:.1f}  {quake.place}  ({minutes:.0f} min fa)  tinta {pulse.frame(0.3, 1.0).hue:.0f}")
    if not is_silent:
        sound.start()
        rumble = sound.save(sound.thunder(1.0, pulse.seconds + 1), "rumble")
        sound.play(rumble, min(1.0, quake.magnitude / FULL_MAGNITUDE))
    play(host, pulse, False)


def run_quakes(host: str, is_replay: bool, min_magnitude: float, is_silent: bool) -> None:
    quakes = tuple(q for q in parse_quakes(json.loads(fetch(QUAKE_FEED))) if q.magnitude >= min_magnitude)
    print(f"{len(quakes)} terremoti da M{min_magnitude} in su nelle ultime 24 ore")
    with borrowed(host):
        for quake in quakes if is_replay else quakes[-1:]:
            show_quake(host, quake, REPLAY_SPEED if is_replay else 1.0, is_silent)
    if is_replay:
        return
    seen = frozenset(quake.key for quake in quakes)
    print("in ascolto: Ctrl-C per fermare")
    while True:
        time.sleep(QUAKE_POLL_S)
        try:
            quakes = parse_quakes(json.loads(fetch(QUAKE_FEED)))
        except (OSError, ValueError) as error:
            print(f"elenco terremoti non letto ({error}), riprovo")
            continue
        for quake in quakes:
            if quake.key not in seen and quake.magnitude >= min_magnitude:
                with borrowed(host):
                    show_quake(host, quake, 1.0, is_silent)
        seen = seen | {quake.key for quake in quakes}


# ---------- the colour of a far-away place ----------

PLACES_FILE = Path(__file__).with_name("places.json")
AREAS = {"tutto": (0.0, 1.0), "cielo": (0.0, 0.4), "centro": (0.3, 0.7)}  # share of the picture's rows, top to bottom
GRID = 16                 # the picture is shrunk to GRID x GRID before averaging
GAMMA = 2.2
SATURATION_BOOST = 2.0    # webcams are greyer than the place looks; a lamp needs the colour pushed
DARK_FLOOR = 0.03         # night over there: the mirror stays barely lit instead of off
PLACE_REFRESH_S = 60
TOUR_HOLD_S = 12


class Place(NamedTuple):
    key: str
    name: str
    area: str
    url: str


def load_places() -> tuple[Place, ...]:
    return tuple(Place(**entry) for entry in json.loads(PLACES_FILE.read_text()))


def bitmap_rows(data: bytes) -> tuple[tuple[tuple[int, int, int], ...], ...]:
    """Pixels of an uncompressed BMP as rows from the top, each pixel (red, green, blue) 0..255."""
    offset = int.from_bytes(data[10:14], "little")
    width = int.from_bytes(data[18:22], "little", signed=True)
    signed_height = int.from_bytes(data[22:26], "little", signed=True)
    step = int.from_bytes(data[28:30], "little") // 8
    stride = (width * step + 3) // 4 * 4
    height = abs(signed_height)

    def pixel(stored: int, column: int) -> tuple[int, int, int]:
        at = offset + stored * stride + column * step
        blue, green, red = data[at:at + 3]
        return red, green, blue

    order = range(height - 1, -1, -1) if signed_height > 0 else range(height)  # stored bottom-up unless height < 0
    return tuple(tuple(pixel(stored, column) for column in range(width)) for stored in order)


def mean_of_bitmap(data: bytes, rows: tuple[float, float]) -> tuple[float, float, float]:
    """Mean colour (in linear light, returned as sRGB 0..1) of a band of rows of the picture."""
    lines = bitmap_rows(data)
    first = int(rows[0] * len(lines))
    band = [pixel for line in lines[first:max(first + 1, int(rows[1] * len(lines)))] for pixel in line]
    red, green, blue = (
        (sum((pixel[channel] / 255) ** GAMMA for pixel in band) / len(band)) ** (1 / GAMMA) for channel in range(3)
    )
    return red, green, blue


def to_bitmap(picture: Path, size: int | None = None) -> bytes:
    """Any picture -> uncompressed BMP bytes with the Mac's own converter, optionally shrunk to size x size."""
    bitmap = picture.with_suffix(".bmp")
    resize = ["-z", str(size), str(size)] if size else []
    subprocess.run(["sips", *resize, "-s", "format", "bmp", str(picture), "--out", str(bitmap)],
                   check=True, capture_output=True)
    return bitmap.read_bytes()


def lamp_colour(rgb: tuple[float, float, float]) -> Hsv:
    hue, saturation, value = colorsys.rgb_to_hsv(*rgb)
    return Hsv(hue * 360, min(1.0, saturation * SATURATION_BOOST), max(DARK_FLOOR, value))


def look_at(place: Place, folder: Path) -> tuple[Hsv, Path]:
    picture = folder / f"{place.key}.jpg"
    picture.write_bytes(fetch(place.url))
    return lamp_colour(mean_of_bitmap(to_bitmap(picture, GRID), AREAS[place.area])), picture


def write_preview(views: list[tuple[Place, Hsv, Path]], folder: Path) -> Path:
    rows = "".join(
        f'<figure><img src="{picture.name}"><div style="background:{as_hex(colour)}"></div>'
        f"<figcaption>{place.name}<br>{as_hex(colour)}</figcaption></figure>"
        for place, colour, picture in views
    )
    page = folder / "anteprima.html"
    page.write_text(
        '<meta charset="utf-8"><title>Lo specchio copia un posto</title><style>body{background:#111;color:#ddd;'
        "font:15px system-ui;display:flex;flex-wrap:wrap;gap:24px;padding:24px}figure{margin:0;width:340px}"
        "img{width:340px;height:200px;object-fit:cover;display:block}div{height:90px}</style>" + rows
    )
    return page


def run_place(host: str, place: Place, seconds: float) -> None:
    folder = Path(tempfile.mkdtemp(prefix="specchio-posti-"))
    started = time.monotonic()
    with borrowed(host):
        while True:
            try:
                colour, _ = look_at(place, folder)
                is_shown = hold(host, colour)
                print(f"{time.strftime('%H:%M')}  {place.name}  {as_hex(colour)}{'' if is_shown else '  (non arrivato)'}")
            except (OSError, subprocess.CalledProcessError, ValueError) as error:
                print(f"{place.name}: immagine non letta ({error}), riprovo")
            left = seconds - (time.monotonic() - started)
            if left <= 0:
                return
            time.sleep(min(PLACE_REFRESH_S, left))


def run_tour(host: str) -> None:
    folder = Path(tempfile.mkdtemp(prefix="specchio-posti-"))
    views = []
    for place in load_places():
        try:
            views.append((place, *look_at(place, folder)))
        except (OSError, subprocess.CalledProcessError, ValueError) as error:
            print(f"{place.name}: saltato ({error})")
    subprocess.run(["open", str(write_preview(views, folder))], check=False)
    with borrowed(host):
        for place, colour, _ in views:
            is_shown = hold(host, colour)
            print(f"{place.name}  {as_hex(colour)}{'' if is_shown else '  (non arrivato)'}")
            time.sleep(TOUR_HOLD_S)


# ---------- planes overhead: the flag of the country they fly to ----------

HERE_FILE = Path(__file__).with_name("qui.txt")  # "latitude,longitude" of the house; kept out of git
PLANES_URL = "https://api.adsb.lol/v2/point/{latitude:.2f}/{longitude:.2f}/{miles:.0f}"
ROUTE_URL = "https://api.adsbdb.com/v0/callsign/{callsign}"
FLAG_URL = "https://flagcdn.com/w80/{iso}.jpg"  # the PNG is paletted and sips cannot turn it into BMP
KM_PER_NAUTICAL_MILE = 1.852
PLANES_POLL_S = 30
FLAG_LEVELS = 4         # channel steps when grouping a flag's pixels: merges the blended edges
FLAG_MIN_SHARE = 0.12   # smaller patches (coats of arms, stars) are not shown
FLAG_COLOUR_S = 1.2
FLAG_ROUNDS = 2


def read_here(text: str) -> tuple[float, float]:
    latitude, longitude = (float(part) for part in text.strip().split(","))
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("latitudine,longitudine fuori dal mondo")
    return latitude, longitude


def planes_near(here: tuple[float, float], km: float) -> tuple[str, ...]:
    """Callsigns of the planes in flight within km, nearest first."""
    url = PLANES_URL.format(latitude=here[0], longitude=here[1], miles=km / KM_PER_NAUTICAL_MILE)
    planes = json.loads(fetch(url)).get("ac", [])
    flying = [plane for plane in planes if plane.get("flight", "").strip() and plane.get("alt_baro") != "ground"]
    return tuple(plane["flight"].strip() for plane in sorted(flying, key=lambda plane: plane.get("dst", 0)))


def destination(callsign: str) -> tuple[str, str] | None:
    """(country code, 'city, country') the flight is going to, None when the route is not public."""
    try:
        airport = json.loads(fetch(ROUTE_URL.format(callsign=callsign)))["response"]["flightroute"]["destination"]
        return airport["country_iso_name"].lower(), f"{airport['municipality']}, {airport['country_name']}"
    except (OSError, ValueError, KeyError, TypeError):
        return None


def flag_colours(lines: tuple[tuple[tuple[int, int, int], ...], ...]) -> tuple[Hsv, ...]:
    """The big flat colours of a flag picture, in reading order (left to right, then down)."""
    groups: dict[tuple[int, ...], list[tuple[int, int, int]]] = collections.defaultdict(list)
    for line in lines:
        for pixel in line:
            groups[tuple(round(channel / 255 * (FLAG_LEVELS - 1)) for channel in pixel)].append(pixel)
    total = sum(len(line) for line in lines)
    colours = []
    for pixels in groups.values():  # dicts keep insertion order: first seen first
        if len(pixels) >= FLAG_MIN_SHARE * total:
            mean = (sum(pixel[channel] for pixel in pixels) / len(pixels) / 255 for channel in range(3))
            hue, saturation, value = colorsys.rgb_to_hsv(*mean)
            colours.append(Hsv(hue * 360, saturation, max(DARK_FLOOR, value)))
    return tuple(colours)


def run_planes(host: str, here: tuple[float, float], km: float, seconds: float) -> None:
    folder = Path(tempfile.mkdtemp(prefix="specchio-aerei-"))
    started = time.monotonic()
    seen: frozenset[str] = frozenset()
    print(f"guardo il cielo nel raggio di {km:.0f} km: Ctrl-C per fermare")
    while True:
        try:
            callsigns = planes_near(here, km)
        except (OSError, ValueError) as error:
            print(f"aerei non letti ({error}), riprovo")
            callsigns = ()
        for callsign in callsigns:
            if callsign in seen:
                continue
            target = destination(callsign)
            if target is None:
                print(f"{callsign}: rotta non pubblica")
                continue
            iso, where = target
            try:
                picture = folder / f"{iso}.jpg"
                picture.write_bytes(fetch(FLAG_URL.format(iso=iso)))
                colours = flag_colours(bitmap_rows(to_bitmap(picture)))
            except (OSError, subprocess.CalledProcessError, ValueError) as error:
                print(f"{callsign} -> {where}: bandiera non letta ({error})")
                continue
            print(f"{callsign} -> {where}  {' '.join(as_hex(colour) for colour in colours)}")
            with borrowed(host):
                for colour in colours * FLAG_ROUNDS:
                    hold(host, colour)
                    time.sleep(FLAG_COLOUR_S)
        seen = seen | frozenset(callsigns)
        left = seconds - (time.monotonic() - started)
        if left <= 0:
            return
        time.sleep(min(PLANES_POLL_S, left))


# ---------- self-check, menu, command line ----------


def check() -> None:
    red_over_blue = (  # 2x2, 24 bit, bottom-up: the first stored row is the bottom one
        b"BM" + (70).to_bytes(4, "little") + bytes(4) + (54).to_bytes(4, "little") + (40).to_bytes(4, "little")
        + (2).to_bytes(4, "little") + (2).to_bytes(4, "little") + (1).to_bytes(2, "little") + (24).to_bytes(2, "little")
        + bytes(24) + b"\xff\x00\x00\xff\x00\x00\x00\x00" + b"\x00\x00\xff\x00\x00\xff\x00\x00"
    )
    assert mean_of_bitmap(red_over_blue, (0.0, 0.5)) == (1.0, 0.0, 0.0), "top half is red"
    assert mean_of_bitmap(red_over_blue, (0.5, 1.0)) == (0.0, 0.0, 1.0), "bottom half is blue"
    assert lamp_colour((0.0, 0.0, 0.0)).value == DARK_FLOOR
    assert [round(colour.hue) for colour in flag_colours(bitmap_rows(red_over_blue))] == [0, 240], "red, then blue"
    assert read_here(" 48.86, 2.35\n") == (48.86, 2.35)

    strike = Strike(10.0, 0.0, ((0.1, 1.0),))
    assert flash_level((strike,), 9.9) == 0 and flash_level((strike,), 10.11) > 0.9
    assert flash_level((strike,), 10.4) < 0.2 and flash_level((strike,), 12.0) == 0
    plan = plan_strikes(STORMS["sopra"], 600)
    gaps = [later.at - sooner.at for sooner, later in zip(plan, plan[1:])]
    assert all(1 <= len(s.strokes) <= 4 and s.at < 600 for s in plan) and min(gaps) > MIN_GAP_S - 1e-6
    assert max(gaps) > 3 * min(gaps), "irregular: bursts and long pauses"
    assert rumble_level((strike,), ((1.0, 0.5),), 10.0) == AFTERGLOW and rumble_level((strike,), ((1.0,),), 9.0) == 0

    feed = {"features": [
        {"id": "b", "properties": {"time": 2000, "mag": 6.0, "place": "B"}, "geometry": {"coordinates": [140.0, 35.0, 10]}},
        {"id": "a", "properties": {"time": 1000, "mag": None, "place": None}, "geometry": {"coordinates": [-120.0, 36.0, 5]}},
    ]}
    first, second = parse_quakes(feed)
    assert (first.key, first.magnitude, first.place) == ("a", 0.0, "?") and second.at == 2.0
    pulse = quake_pulse(second)
    assert pulse.seconds == QUAKE_BASE_S + 6.0 and pulse.frame(0.3, pulse.seconds).hue == 320.0
    assert quake_pulse(second, 3.0).seconds == pulse.seconds / 3
    assert all(place.area in AREAS and place.url.startswith("https://") for place in load_places())
    assert len({entry.key for entry in load_sounds()}) == len(load_sounds()) > 0
    frame = thunder_frame((0.0, 0.0, 1.0, 0.2), 0.0)
    assert frame(-1.0, 9) == STORM_LIGHT and frame(0.0, 9) == STORM_LIGHT and frame(9.0, 9) == STORM_LIGHT
    crack = frame(2.2 * sound.ENVELOPE_STEP_S - NET_LAG_S, 9)   # the frame is drawn NET_LAG_S ahead of the sound
    assert crack.value == 1.0 and crack.saturation == 0
    sound.demo()
    print("scenes ok")


def menu(host: str) -> None:
    entries: list[tuple[str, Callable[[], None]]] = [
        *((f"temporale {name} (1 minuto)", lambda name=name: run_storm(host, name, 60, False)) for name in STORMS),
        ("terremoti: le ultime 24 ore di fila", lambda: run_quakes(host, True, 2.5, False)),
        ("terremoti: dal vivo", lambda: run_quakes(host, False, 2.5, False)),
        ("posti: giro di tutti, con anteprima", lambda: run_tour(host)),
        *((f"posto: {place.name}", lambda place=place: run_place(host, place, math.inf)) for place in load_places()),
    ]
    if HERE_FILE.exists():
        here = read_here(HERE_FILE.read_text())
        entries.append(("aerei sopra casa: bandiera della destinazione", lambda: run_planes(host, here, 15, math.inf)))
    while True:
        print()
        for number, (label, _) in enumerate(entries, 1):
            print(f"{number:2}  {label}")
        answer = input("numero (Ctrl-C ferma una scena, q esce): ").strip()
        if answer == "q":
            return
        if not answer.isdigit() or not 1 <= int(answer) <= len(entries):
            continue
        try:
            entries[int(answer) - 1][1]()
        except KeyboardInterrupt:
            print("\nfermata")
        except OSError as error:
            print(f"non riuscita: {error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=HOST)
    scenes = parser.add_subparsers(dest="scene")
    storm = scenes.add_parser("temporale")
    storm.add_argument("name", nargs="?", default="lontano", choices=STORMS)
    storm.add_argument("--seconds", type=float, default=60)
    storm.add_argument("--muto", action="store_true")
    storm.add_argument("--anticipo", type=float, default=SOUND_LEAD_S,
                       help="seconds the thunder is launched before its flash; raise it if the sound comes late")
    quakes = scenes.add_parser("terremoti")
    quakes.add_argument("--replay", action="store_true")
    quakes.add_argument("--min-mag", type=float, default=2.5)
    quakes.add_argument("--muto", action="store_true")
    place = scenes.add_parser("posto")
    place.add_argument("place", nargs="?", help="a key from places.json or the URL of a still picture")
    place.add_argument("--area", default="tutto", choices=AREAS, help="for a URL: which part of the picture to copy")
    place.add_argument("--giro", action="store_true")
    place.add_argument("--seconds", type=float, default=math.inf)
    planes = scenes.add_parser("aerei")
    planes.add_argument("--qui", help=f"latitude,longitude of the house (default: the content of {HERE_FILE.name})")
    planes.add_argument("--km", type=float, default=15)
    planes.add_argument("--seconds", type=float, default=math.inf)
    sounds = scenes.add_parser("suoni")
    sounds.add_argument("key", nargs="?", help="a key from sounds.json; without it, a menu")
    sounds.add_argument("--seconds", type=float, default=LISTEN_S)
    sounds.add_argument("--anticipo", type=float, default=SOUND_LEAD_S,
                        help="seconds the Mac needs to start the sound; raise it if the light comes before the thunder")
    scenes.add_parser("check")
    args = parser.parse_args()

    try:
        if args.scene == "temporale":
            run_storm(args.host, args.name, args.seconds, args.muto, args.anticipo)
        elif args.scene == "terremoti":
            run_quakes(args.host, args.replay, args.min_mag, args.muto)
        elif args.scene == "posto" and args.giro:
            run_tour(args.host)
        elif args.scene == "posto":
            known = {entry.key: entry for entry in load_places()}
            if args.place not in known and not str(args.place).startswith("http"):
                parser.error(f"posto: uno tra {', '.join(known)} oppure un indirizzo http")
            run_place(args.host, known.get(args.place) or Place("url", args.place, args.area, args.place), args.seconds)
        elif args.scene == "aerei":
            if not args.qui and not HERE_FILE.exists():
                parser.error(f"aerei: serve --qui LAT,LON oppure il file {HERE_FILE.name} con dentro LAT,LON")
            run_planes(args.host, read_here(args.qui or HERE_FILE.read_text()), args.km, args.seconds)
        elif args.scene == "suoni" and args.key:
            by_key = {entry.key: entry for entry in load_sounds()}
            if args.key not in by_key:
                parser.error(f"suoni: uno tra {', '.join(by_key)}")
            listen(args.host, by_key[args.key], args.seconds, args.anticipo)
        elif args.scene == "suoni":
            sounds_menu(args.host, args.seconds, args.anticipo)
        elif args.scene == "check":
            check()
        else:
            menu(args.host)
    except KeyboardInterrupt:
        print("\nfermata")


if __name__ == "__main__":
    main()
