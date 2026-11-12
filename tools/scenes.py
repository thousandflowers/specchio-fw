#!/usr/bin/env python3
"""Scenes for the mirror, driven live from the Mac: light on the strip, sound from the Mac.

    scenes.py                        # menu: pick a scene by number
    scenes.py temporale film         # real rain and the film thunder, at random, until stopped
    scenes.py temporale mare         # a stormy sea: the light swells with the waves
    scenes.py luogo Tokyo            # the sky over any place right now: sun height, clouds, wind, rain, storms
    scenes.py luogo Tokyo --alba     # that place's sunrise in three minutes (--tramonto for the sunset)
    scenes.py sole                   # the Sun's own activity, live; --replay plays the last six hours
    scenes.py sonno                  # falling asleep: dim amber light to breathe with, rain fading out, then dark
    scenes.py temporale sopra        # synthetic thunderstorm, one minute; also: lontano, notte
    scenes.py terremoti              # live: one pulse for every earthquake in the world
    scenes.py terremoti --replay     # the last 24 hours, one after the other
    scenes.py posto kilauea          # the mirror takes the colour of a far-away webcam
    scenes.py posto --giro           # every place in places.json, plus a preview page
    scenes.py aerei --qui 48.86,2.35 # planes overhead: the flag colours of the country they fly to
    scenes.py suoni                  # real recordings, one at a time: the light follows the thunder
    scenes.py aria                   # watch the air: normal light, coloured blinks when it gets bad
    scenes.py aria --sempre          # the mirror stays the colour of the air (PM2.5)
    scenes.py aria --prova           # the whole scale in 20 seconds, from clean to very bad
    scenes.py casa                   # follow the scene switches of the Home app: on starts, off stops
    scenes.py check                  # self-check, touches neither lamp nor network

Ctrl-C stops a scene; the lamp goes back to what it was showing.
"""
import argparse
import collections
import colorsys
import itertools
import json
import math
import os
import random
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, NamedTuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import nature
import purifier
import sound
import globe
from demo_effects import Effect, Hsv, lan_get, local_setting, play, read_local, send, wave

HOST = local_setting("strip", "specchio.local")   # this home's values live in tools/local.json
NET_TIMEOUT_S = 12
TRIES = 3

# ---------- the lamp ----------


def fetch(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "curl/8"}), timeout=NET_TIMEOUT_S) as reply:
        return reply.read()


WAKE_CHECK_S = 10
WAKE_IDLE = ("", "waiting")   # wake_phase values that mean no wake-up light is showing


def is_wake_running(state: bytes | None) -> bool:
    """True while the strip shows its wake-up light: then the lamp is not ours to touch."""
    try:
        return json.loads(state or b"{}").get("wake_phase", "") not in WAKE_IDLE
    except ValueError:
        return False


def yield_to_wake(host: str, is_over: threading.Event) -> None:
    """Stop the scene (as Ctrl-C would) as soon as the wake-up starts: the firmware refuses our frames anyway."""
    while not is_over.wait(WAKE_CHECK_S):
        if is_wake_running(lan_get(host, "/api/state")):
            print("sveglia in corso: lascio la luce alla sveglia")
            os.kill(os.getpid(), signal.SIGINT)
            return


@contextmanager
def borrowed(host: str, ends_dark: bool = False) -> Iterator[None]:
    """Raw frames take the lamp over: afterwards give it back exactly as it was, unless a wake-up took it.

    ends_dark: a scene that ran to its end leaves the lamp off (falling asleep); stopped early, it is restored.
    """
    state = lan_get(host, "/api/state")
    if state is None:
        raise OSError(f"la striscia non risponde a {host}")
    if is_wake_running(state):
        raise OSError("sveglia in corso: la luce è della sveglia")
    before = json.loads(state)
    is_over = threading.Event()
    threading.Thread(target=yield_to_wake, args=(host, is_over), daemon=True).start()
    has_finished = False
    try:
        yield
        has_finished = True
    finally:
        is_over.set()
        sound.stop()
        # During a wake-up, setting the light would count as "changed by hand" and cancel it: leave it alone.
        stays_on = before["on"] and not (ends_dark and has_finished)
        query = urlencode({"on": int(stays_on), "h": before["h"], "s": before["s"], "v": before["v"]})
        if not is_wake_running(lan_get(host, "/api/state")) and not any(
                lan_get(host, f"/api/set?{query}") is not None for _ in range(TRIES)):
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
STEADY_KINDS = frozenset({"pioggia", "mare", "vento"})   # beds of sound: nothing the light should flash for


class Sound(NamedTuple):
    key: str
    name: str
    kind: str
    author: str
    licence: str
    url: str


def load_sounds() -> tuple[Sound, ...]:
    return tuple(Sound(**entry) for entry in json.loads(SOUNDS_FILE.read_text()))


def thunder_level(shape: tuple[float, ...], since: float) -> float:
    """How bright a thunder makes the light, `since` seconds after its sound became audible."""
    index = int((since + NET_LAG_S) / sound.ENVELOPE_STEP_S)
    if not 0 <= index < len(shape):
        return 0.0
    rise = max(0.0, shape[index] - shape[max(0, index - 2)])
    return min(1.0, shape[index] + FLASH_KICK * rise)


def storm_light(level: float, base: Hsv = STORM_LIGHT) -> Hsv:
    return Hsv(base.hue, base.saturation * (1 - level), base.value + (1 - base.value) * level)


def thunder_frame(shape: tuple[float, ...], sound_started_s: float) -> Callable[[float, float], Hsv]:
    """Brightness follows the thunder's loudness; sound_started_s = when the sound becomes audible on the frame clock."""
    return lambda elapsed, _total: storm_light(thunder_level(shape, elapsed - sound_started_s))


class RealStorm(NamedTuple):
    beds: tuple[tuple[str, float], ...]   # (key from sounds.json, volume): sounds that loop the whole time
    thunders: tuple[str, ...]
    mean_gap_s: float            # thunders start at random (Poisson) around this mean
    volume: tuple[float, float]  # each thunder gets a random loudness in this range: near and far
    light: Hsv                   # the light while nothing is sounding
    swell: float                 # how much the first bed's own loudness moves the light: waves yes, rain no


REAL_STORMS = {
    "film": RealStorm((("pioggia", 0.6),), ("tuono-teatro",), 35, (0.45, 1.0), STORM_LIGHT, 0.0),
    "mare": RealStorm((("mare", 0.9), ("vento", 0.5)), ("tuono-teatro",), 70, (0.3, 0.7), Hsv(196, 0.80, 0.10), 0.75),
}
REAL_MIN_GAP_S = 4.0
STORM_CHUNK_S = 600.0    # an endless storm is planned this much at a time


class Clap(NamedTuple):
    at: float
    thunder: int      # index into the storm's recordings
    volume: float


def plan_claps(storm: RealStorm, seconds: float, longest_s: float) -> tuple[Clap, ...]:
    """Random claps, none so late that its tail would be cut (unless the whole stretch is short)."""
    last = seconds - longest_s if seconds > 2 * longest_s else seconds - 2
    claps = []
    at = random.uniform(1.5, 5.0)
    while at < last:
        claps.append(Clap(at, random.randrange(len(storm.thunders)), random.uniform(*storm.volume)))
        at += max(REAL_MIN_GAP_S, random.expovariate(1 / storm.mean_gap_s))
    return tuple(claps)


def run_real_storm(host: str, name: str, seconds: float, lead_s: float) -> None:
    storm = REAL_STORMS[name]
    by_key = {entry.key: entry for entry in load_sounds()}
    beds = tuple((sound.recording(by_key[key].url), volume) for key, volume in storm.beds)
    thunders = tuple(sound.recording(by_key[key].url) for key in storm.thunders)
    longest = max(thunder.seconds for thunder in thunders)
    swell = sound.swell_shape(beds[0][0]) if storm.swell else ()
    swell_period = beds[0][0].seconds - sound.CROSSFADE_S
    print(f"temporale {name}: Ctrl-C per fermare" if math.isinf(seconds) else f"temporale {name}: {seconds:.0f} s")
    with borrowed(host):
        sound.start()
        for bed, volume in beds:
            sound.repeat(sound.faded(bed), bed.seconds, volume)
        scene_started = time.monotonic()
        left = seconds
        while left > 0:
            span = min(left, STORM_CHUNK_S)
            claps = plan_claps(storm, span, longest)
            for clap in claps:
                sound.later(clap.at, thunders[clap.thunder].path, clap.volume)

            def frame(elapsed: float, _total: float, claps: tuple[Clap, ...] = claps,
                      chunk_at: float = time.monotonic() - scene_started) -> Hsv:
                thunder = sum(clap.volume * thunder_level(thunders[clap.thunder].thunder, elapsed - clap.at - lead_s)
                              for clap in claps)
                beat = int(((chunk_at + elapsed - lead_s) % swell_period) / sound.ENVELOPE_STEP_S)
                wave = storm.swell * swell[beat] if beat < len(swell) else 0.0
                return storm_light(min(1.0, thunder + wave), storm.light)

            sent, lost = play(host, Effect(name, name, span, frame), False)
            print(f"{time.strftime('%H:%M')}  {len(claps)} tuoni, {sent / span:.0f} fotogrammi/s, {lost} persi")
            left -= span


def listen(host: str, entry: Sound, seconds: float, lead_s: float) -> None:
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


# ---------- living places: the sky over anywhere, right now ----------

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search?"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast?"
WEATHER_REFRESH_S = 600
DAWN_SPAN = (-14.0, 12.0)   # the sun heights a replayed sunrise runs through (a sunset runs them backwards)
DAWN_REPLAY_S = 180


class Spot(NamedTuple):
    name: str
    latitude: float
    longitude: float


class Weather(NamedTuple):
    cloud: float       # 0..1
    wind_kmh: float    # gusts
    rain_mm: float
    is_thunder: bool


CALM = Weather(0.0, 0.0, 0.0, False)


def find_spot(name: str) -> Spot:
    reply = json.loads(fetch(GEOCODE_URL + urlencode({"name": name, "count": 1, "language": "it", "format": "json"})))
    if not reply.get("results"):
        raise OSError(f"luogo non trovato: {name}")
    place = reply["results"][0]
    return Spot(", ".join(filter(None, (place["name"], place.get("country")))), place["latitude"], place["longitude"])


def read_weather(spot: Spot) -> Weather:
    query = urlencode({"latitude": f"{spot.latitude:.2f}", "longitude": f"{spot.longitude:.2f}",
                       "current": "cloud_cover,wind_gusts_10m,precipitation,weather_code"})
    now = json.loads(fetch(WEATHER_URL + query))["current"]
    return Weather(now["cloud_cover"] / 100, now["wind_gusts_10m"], now["precipitation"],
                   now["weather_code"] in nature.THUNDER_CODES)


def spot_frame(weather: Weather, strikes: tuple[Strike, ...], height: Callable[[float], float]) -> Callable[[float, float], Hsv]:
    """The sky for the sun's height, swayed by the wind, trembling in the rain, torn by lightning in a storm."""
    def frame(elapsed: float, _total: float) -> Hsv:
        sky = nature.sky_colour(height(elapsed), weather.cloud)
        value = min(1.0, sky.value * nature.weather_wobble(elapsed, weather.wind_kmh, weather.rain_mm))
        level = flash_level(strikes, elapsed)
        return Hsv(sky.hue, sky.saturation * (1 - level), value + (1 - value) * level)
    return frame


def saved_spot() -> Spot:
    """The place picked on the globe, or the home; error when neither is set."""
    local = read_local()
    place = local.get("place")
    if isinstance(place, dict):
        return Spot(str(place["name"]), float(place["lat"]), float(place["lon"]))
    if local.get("home"):
        latitude, longitude = read_here(str(local["home"]))
        return Spot("casa", latitude, longitude)
    raise OSError("nessun luogo scelto: usa il globo (scenes.py casa, poi la pagina sul telefono) o scrivi un nome")


def run_spot(host: str, name: str, seconds: float, replay: str) -> None:
    """replay: "" follows the real sun; "alba" or "tramonto" plays that place's dawn or dusk in three minutes.

    Without a name: the place picked on the globe (tools/local.json).
    """
    spot = find_spot(name) if name else saved_spot()
    weather = CALM
    left = DAWN_REPLAY_S if replay else seconds
    with borrowed(host):
        while left > 0:
            span = min(left, WEATHER_REFRESH_S)
            try:
                weather = read_weather(spot)
            except (OSError, KeyError, ValueError) as error:
                print(f"meteo non letto ({error}), tengo quello di prima")
            if replay:
                first, last = DAWN_SPAN if replay == "alba" else DAWN_SPAN[::-1]
                height = lambda elapsed, first=first, last=last, span=span: first + (last - first) * elapsed / span
            else:
                height = lambda elapsed, at=time.time(): nature.sun_elevation(spot.latitude, spot.longitude, at + elapsed)
            strikes = plan_strikes(STORMS["lontano"], span) if weather.is_thunder else ()
            print(f"{time.strftime('%H:%M')}  {spot.name}: sole a {height(0):.0f}°, nuvole {weather.cloud:.0%}, "
                  f"raffiche {weather.wind_kmh:.0f} km/h, pioggia {weather.rain_mm} mm"
                  f"{', temporale' if weather.is_thunder else ''}  {as_hex(nature.sky_colour(height(0), weather.cloud))}")
            play(host, Effect("luogo", spot.name, span, spot_frame(weather, strikes, height)), False)
            left -= span


# ---------- the Sun itself: its X-ray flux, measured by the GOES satellites ----------

XRAY_URL = "https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json"
XRAY_BAND = "0.1-0.8nm"     # the band flare classes are defined on
SUN_REFRESH_S = 60
SUN_REPLAY_S = 120


def long_band(entries: list[dict]) -> tuple[float, ...]:
    """Flux of the flare band, oldest first; gaps (zero or missing) are dropped."""
    return tuple(entry["flux"] for entry in entries if entry.get("energy") == XRAY_BAND and (entry.get("flux") or 0) > 0)


def run_sun(host: str, seconds: float, is_replay: bool) -> None:
    series = long_band(json.loads(fetch(XRAY_URL)))
    if not series:
        raise OSError("nessuna misura del Sole disponibile")
    with borrowed(host):
        if is_replay:
            print(f"Sole, ultime 6 ore in {SUN_REPLAY_S / 60:.0f} minuti: da {nature.flare_class(min(series))} "
                  f"a {nature.flare_class(max(series))}")

            def frame(elapsed: float, total: float) -> Hsv:
                return nature.sun_colour(nature.flare_level(series[min(len(series) - 1, int(elapsed / total * len(series)))]), elapsed)

            play(host, Effect("sole", "sole", SUN_REPLAY_S, frame), False)
            return
        started = time.monotonic()
        left = seconds
        while left > 0:
            span = min(left, SUN_REFRESH_S)
            try:
                series = long_band(json.loads(fetch(XRAY_URL))) or series
            except (OSError, ValueError) as error:
                print(f"Sole non letto ({error}), tengo l'ultima misura")
            level = nature.flare_level(series[-1])
            print(f"{time.strftime('%H:%M')}  Sole: classe {nature.flare_class(series[-1])}  {as_hex(nature.sun_colour(level, 0))}")
            play(host, Effect("sole", "sole", span, lambda elapsed, _total, level=level, at=time.monotonic() - started:
                              nature.sun_colour(level, at + elapsed)), False)
            left -= span


# ---------- falling asleep ----------

SLEEP_MINUTES = 20
SLEEP_SOUND = "pioggia"
SLEEP_VOLUME = 0.5
SLEEP_SOUND_FADE = 0.4   # the sound fades over this last share of the time


def sleep_volume(progress: float) -> float:
    return SLEEP_VOLUME * min(1.0, max(0.0, 1 - progress) / SLEEP_SOUND_FADE)


def run_sleep(host: str, minutes: float, sound_key: str) -> None:
    """Dim amber light to breathe with (ten breaths a minute slowing to six), rain fading out, then dark."""
    total = minutes * 60
    by_key = {entry.key: entry for entry in load_sounds()}
    if sound_key not in by_key:
        raise OSError(f"suono sconosciuto: {sound_key} (uno tra {', '.join(by_key)})")
    bed = sound.recording(by_key[sound_key].url)
    print(f"sonno: {minutes:.0f} minuti, poi buio. Inspira quando la luce sale, espira quando scende.")
    with borrowed(host, ends_dark=True):
        sound.start()
        started = time.monotonic()
        sound.repeat(sound.faded(bed), bed.seconds, lambda: sleep_volume((time.monotonic() - started) / total))
        play(host, Effect("sonno", "sonno", total, nature.sleep_light), False)


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

HERE = local_setting("home", "")   # "latitude,longitude" of the house, from tools/local.json
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


# ---------- air quality: the colour of the air in the room ----------

AIR_SCALE = ((0, 120), (25, 60), (50, 30), (100, 0), (200, -70))  # PM2.5 in µg/m³ -> hue: green, yellow, orange, red, purple
AIR_LIGHT = 0.6
AIR_REFRESH_S = 60
AIR_SWEEP_S = 20


def air_colour(pm25: float) -> Hsv:
    """Hue slides along AIR_SCALE: clean air is green, the worst the purifier can report is purple."""
    clamped = min(max(pm25, AIR_SCALE[0][0]), AIR_SCALE[-1][0])
    for (low, low_hue), (high, high_hue) in zip(AIR_SCALE, AIR_SCALE[1:]):
        if clamped <= high:
            return Hsv((low_hue + (high_hue - low_hue) * (clamped - low) / (high - low)) % 360, 1.0, AIR_LIGHT)
    return Hsv(AIR_SCALE[-1][1] % 360, 1.0, AIR_LIGHT)


class AirRule(NamedTuple):
    reading: str       # a name from purifier.READINGS
    limit: float
    is_above: bool     # broken when the reading is at the limit or beyond it on this side
    hex: str           # colour of the blinks
    blinks: int
    remind_s: float    # while it stays broken, blink again this often
    message: str


# For one reading the worst rule comes first: only the first broken one speaks.
AIR_RULES = (
    AirRule("pm2.5-density", 50, True, "ff0000", 5, 15 * 60, "molta polvere o fumo nell'aria"),
    AirRule("pm2.5-density", 25, True, "ff6a00", 3, 15 * 60, "polvere o fumo nell'aria"),
    AirRule("relative-humidity", 65, True, "0050ff", 3, 2 * 3600, "aria umida e ferma: apri la finestra"),
    AirRule("filter-life-level", 5, False, "ff00ff", 2, 24 * 3600, "filtro del purificatore finito: pulisce poco"),
)
AIR_LABELS = {"pm2.5-density": "polvere {} µg/m³", "relative-humidity": "umidità {}%",
              "temperature": "{}°C", "filter-life-level": "filtro {}%"}
AIR_MARGIN = 0.1            # a broken rule heals only once the reading is this share of the limit back inside
ALL_CLEAR_HEX = "00ff40"
QUIET_HOURS = (23, 8)       # no blinks from 23:00 to 08:00


def is_broken(rule: AirRule, value: float, was_broken: bool) -> bool:
    limit = rule.limit * ((1 - AIR_MARGIN if rule.is_above else 1 + AIR_MARGIN) if was_broken else 1)
    return value >= limit if rule.is_above else value <= limit


def broken_rules(readings: dict[str, float], before: tuple[AirRule, ...]) -> tuple[AirRule, ...]:
    """The worst broken rule of each reading."""
    worst: dict[str, AirRule] = {}
    for rule in AIR_RULES:
        if rule.reading in readings and rule.reading not in worst and is_broken(rule, readings[rule.reading], rule in before):
            worst[rule.reading] = rule
    return tuple(worst.values())


def is_quiet(hour: int) -> bool:
    start, end = QUIET_HOURS
    return hour >= start or hour < end


def watch_air(host: str, seconds: float, is_night_allowed: bool) -> None:
    """The lamp keeps its own light; the firmware blinks a colour over it when a rule breaks."""
    started = time.monotonic()
    broken: tuple[AirRule, ...] = ()
    blinked_at: dict[AirRule, float] = {}
    print("controllo l'aria ogni minuto: Ctrl-C per fermare")
    while True:
        try:
            readings = purifier.read_air()
            now_broken = broken_rules(readings, broken)
            can_blink = is_night_allowed or not is_quiet(time.localtime().tm_hour)
            print(f"{time.strftime('%H:%M')}  " + "  ".join(AIR_LABELS[name].format(value) for name, value in readings.items())
                  + "".join(f"\n       ! {rule.message}" for rule in now_broken)
                  + ("" if can_blink or not now_broken else "\n       (di notte non lampeggio)"))
            for rule in now_broken:
                if can_blink and time.monotonic() - blinked_at.get(rule, -math.inf) >= rule.remind_s:
                    if lan_get(host, f"/api/flash?hex={rule.hex}&n={rule.blinks}") is None:
                        print("       (lampeggio non arrivato alla striscia)")
                        continue
                    blinked_at = {**blinked_at, rule: time.monotonic()}
                    time.sleep(rule.blinks)   # let one colour finish before the next
            if broken and not now_broken:
                print("       aria tornata buona")
                if can_blink:
                    lan_get(host, f"/api/flash?hex={ALL_CLEAR_HEX}&n=1")
            blinked_at = {rule: at for rule, at in blinked_at.items() if rule in now_broken}
            broken = now_broken
        except (OSError, KeyError, ValueError) as error:
            print(f"purificatore non letto ({error}), riprovo")
        left = seconds - (time.monotonic() - started)
        if left <= 0:
            return
        time.sleep(min(AIR_REFRESH_S, left))


def run_air(host: str, seconds: float, is_sweep: bool) -> None:
    if is_sweep:
        worst = AIR_SCALE[-1][0]
        with borrowed(host):
            play(host, Effect("aria", "aria", AIR_SWEEP_S, lambda elapsed, total: air_colour(worst * elapsed / total)), False)
        return
    started = time.monotonic()
    with borrowed(host):
        while True:
            try:
                pm25 = purifier.read_pm25()
                is_shown = hold(host, air_colour(pm25))
                print(f"{time.strftime('%H:%M')}  PM2.5 {pm25} µg/m³  {as_hex(air_colour(pm25))}{'' if is_shown else '  (non arrivato)'}")
            except (OSError, KeyError, ValueError) as error:
                print(f"purificatore non letto ({error}), riprovo")
            left = seconds - (time.monotonic() - started)
            if left <= 0:
                return
            time.sleep(min(AIR_REFRESH_S, left))


# ---------- Home app: the strip holds one switch per scene, this follows them ----------

HOME_POLL_S = 2
HOME_STOP_S = 8     # a scene gets this long to put the lamp back before it is killed


class HomeScene(NamedTuple):
    arguments: tuple[str, ...]   # what to run: scenes.py <arguments>
    owns_lamp: bool              # drives the light all the time: only one of these at once


def home_scenes() -> dict[str, HomeScene]:
    """Keys are the firmware's scene switches (SCENES in src/main.cpp)."""
    return {
        "temporale": HomeScene(("temporale", next(iter(REAL_STORMS))), True),
        "posto": HomeScene(("posto", load_places()[0].key), True),
        "aria": HomeScene(("aria",), False),
        "terremoti": HomeScene(("terremoti",), False),
        "aerei": HomeScene(("aerei",), False),
        "mare": HomeScene(("temporale", "mare"), True),
        "luogo": HomeScene(("luogo",), True),
        "sole": HomeScene(("sole",), True),
        "sonno": HomeScene(("sonno",), True),
    }


def to_drop(wanted: frozenset[str], running: frozenset[str], scenes: dict[str, HomeScene]) -> frozenset[str]:
    """Lamp-owning scenes that must give way: a newly switched-on one wins over those already playing."""
    owners = frozenset(key for key in wanted if scenes[key].owns_lamp)
    fresh = owners - running
    if len(owners) <= 1:
        return frozenset()
    keep = sorted(fresh)[0] if fresh else sorted(owners)[0]
    return owners - {keep}


def stop_scene(process: subprocess.Popen) -> None:
    process.terminate()   # main() turns this into the same clean stop as Ctrl-C: sound off, lamp restored
    try:
        process.wait(HOME_STOP_S)
    except subprocess.TimeoutExpired:
        process.kill()


def run_home(host: str) -> None:
    scenes = home_scenes()
    running: dict[str, subprocess.Popen] = {}
    print("seguo gli interruttori dell'app Casa: Ctrl-C per fermare")
    page = globe.serve(host)
    if page:
        name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True).stdout.strip()
        print(f"globo per scegliere il luogo: http://{name or 'questo-mac'}.local:{globe.PORT}")
    place_seen = read_local().get("place")
    parent = os.getppid()   # started by the auto-start service: when that stops, stop too and take the scenes along
    try:
        while os.getppid() == parent:
            place_now = read_local().get("place")
            if place_now != place_seen and "luogo" in running:
                print(f"{time.strftime('%H:%M')}  luogo: nuovo posto, riparto")
                stop_scene(running.pop("luogo"))
            place_seen = place_now
            state = lan_get(host, "/api/state")
            listed = json.loads(state).get("scenes") if state else None
            if listed is None:
                print("la striscia non risponde o ha un firmware senza scene" if state else "la striscia non risponde")
                time.sleep(HOME_POLL_S * 5)
                continue
            wanted = frozenset(key for key in listed.split(",") if key in scenes)
            for key in to_drop(wanted, frozenset(running), scenes):
                lan_get(host, f"/api/scene?name={key}&on=0")
                wanted = wanted - {key}
            for key in list(running):
                if key not in wanted:
                    print(f"{time.strftime('%H:%M')}  {key}: spento")
                    stop_scene(running.pop(key))
                elif running[key].poll() is not None:
                    print(f"{time.strftime('%H:%M')}  {key}: finito da solo, spengo l'interruttore")
                    del running[key]
                    lan_get(host, f"/api/scene?name={key}&on=0")
                    wanted = wanted - {key}
            for key in wanted - frozenset(running):
                print(f"{time.strftime('%H:%M')}  {key}: acceso")
                running[key] = subprocess.Popen([sys.executable, __file__, "--host", host, *scenes[key].arguments])
            time.sleep(HOME_POLL_S)
    finally:
        if page:
            page.shutdown()
        for process in running.values():
            stop_scene(process)


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
    assert [round(air_colour(value).hue) for value in (-5, 0, 25, 37.5, 100, 150, 9999)] == [120, 120, 60, 45, 0, 325, 290]
    strong, mild, damp, spent = AIR_RULES
    assert broken_rules({"pm2.5-density": 3, "relative-humidity": 50, "filter-life-level": 80}, ()) == ()
    assert broken_rules({"pm2.5-density": 60, "relative-humidity": 68}, ()) == (strong, damp), "worst dust rule only"
    assert broken_rules({"pm2.5-density": 24}, ()) == () and broken_rules({"pm2.5-density": 24}, (mild,)) == (mild,)
    assert broken_rules({"pm2.5-density": 22}, (mild,)) == (), "heals once clearly back inside"
    assert broken_rules({"filter-life-level": 0}, ()) == (spent,) and broken_rules({"temperature": 99}, ()) == ()
    assert is_quiet(23) and is_quiet(3) and not is_quiet(8) and not is_quiet(22)
    assert set(AIR_LABELS) == set(purifier.READINGS) >= {rule.reading for rule in AIR_RULES}
    assert not is_wake_running(b'{"wake_phase":""}') and not is_wake_running(b'{"wake_phase":"waiting"}')
    assert is_wake_running(b'{"wake_phase":"sunrise"}') and not is_wake_running(None) and not is_wake_running(b"x")
    assert sleep_volume(0) == SLEEP_VOLUME and abs(sleep_volume(0.8) - SLEEP_VOLUME / 2) < 1e-9 and sleep_volume(1) == 0
    stormy = spot_frame(Weather(1.0, 60, 3, True), (Strike(5.0, 0.0, ((0.0, 1.0),)),), lambda elapsed: 40.0)
    assert stormy(5.01, 9).saturation < 0.01 and stormy(5.01, 9).value > 0.9, "lightning whitens the sky"
    assert spot_frame(CALM, (), lambda elapsed: -30.0)(1, 9) == nature.SKY[0][1], "a calm night is the night colour"
    assert long_band([{"energy": "0.1-0.8nm", "flux": 2e-6}, {"energy": "0.05-0.4nm", "flux": 9}, {"energy": "0.1-0.8nm", "flux": 0}]) == (2e-6,)
    nature.demo()
    assert globe.clean_place({"lat": "45.5", "lon": 12.3, "name": "Ve\nnezia"}) == {"name": "Venezia", "lat": 45.5, "lon": 12.3}
    assert globe.clean_place({"lat": 1, "lon": 2})["name"] == "1.00, 2.00"
    for bad in ({"lat": 91, "lon": 0}, {"lat": "x", "lon": 0}, {"lon": 0}):
        try:
            globe.clean_place(bad)
            raise AssertionError(f"accepted {bad}")
        except (KeyError, ValueError):
            pass
    home = home_scenes()
    assert to_drop(frozenset({"temporale", "aria"}), frozenset(), home) == frozenset(), "the air watch lives with a scene"
    assert to_drop(frozenset({"temporale", "posto"}), frozenset({"temporale"}), home) == {"temporale"}, "the new one wins"
    assert to_drop(frozenset({"temporale", "posto"}), frozenset(), home) == {"temporale"}, "both at once: one stays"
    frame = thunder_frame((0.0, 0.0, 1.0, 0.2), 0.0)
    assert frame(-1.0, 9) == STORM_LIGHT and frame(0.0, 9) == STORM_LIGHT and frame(9.0, 9) == STORM_LIGHT
    keys = {entry.key for entry in load_sounds()}
    assert all({*(key for key, _ in storm.beds), *storm.thunders} <= keys for storm in REAL_STORMS.values()), "storms use listed sounds"
    claps = plan_claps(REAL_STORMS["film"], 3600, 25)
    assert claps and all(clap.at < 3600 - 25 for clap in claps), "no thunder cut by the end of a stretch"
    assert min(b.at - a.at for a, b in zip(claps, claps[1:])) > REAL_MIN_GAP_S - 1e-6
    assert plan_claps(REAL_STORMS["film"], 20, 25), "a short test still gets a thunder"
    crack = frame(2.2 * sound.ENVELOPE_STEP_S - NET_LAG_S, 9)   # the frame is drawn NET_LAG_S ahead of the sound
    assert crack.value == 1.0 and crack.saturation == 0
    sound.demo()
    print("scenes ok")


def menu(host: str) -> None:
    entries: list[tuple[str, Callable[[], None]]] = [
        *((f"temporale {name}: suoni veri, senza fine", lambda name=name: run_real_storm(host, name, math.inf, SOUND_LEAD_S))
          for name in REAL_STORMS),
        ("suoni: ascolta le registrazioni una alla volta", lambda: sounds_menu(host, LISTEN_S, SOUND_LEAD_S)),
        (f"sonno: luce per respirare e pioggia che sfuma, {SLEEP_MINUTES} minuti, poi buio",
         lambda: run_sleep(host, SLEEP_MINUTES, SLEEP_SOUND)),
        ("luogo: il cielo di un posto adesso (ti chiedo il nome)", lambda: run_spot(host, input("posto: ").strip(), math.inf, "")),
        ("luogo: alba di un posto in 3 minuti (ti chiedo il nome)", lambda: run_spot(host, input("posto: ").strip(), 0, "alba")),
        ("luogo: tramonto di un posto in 3 minuti (ti chiedo il nome)", lambda: run_spot(host, input("posto: ").strip(), 0, "tramonto")),
        ("sole: la sua attività adesso", lambda: run_sun(host, math.inf, False)),
        ("sole: le ultime 6 ore in 2 minuti", lambda: run_sun(host, 0, True)),
        *((f"temporale {name}: suoni finti, 1 minuto", lambda name=name: run_storm(host, name, 60, False)) for name in STORMS),
        ("terremoti: le ultime 24 ore di fila", lambda: run_quakes(host, True, 2.5, False)),
        ("terremoti: dal vivo", lambda: run_quakes(host, False, 2.5, False)),
        ("posti: giro di tutti, con anteprima", lambda: run_tour(host)),
        *((f"posto: {place.name}", lambda place=place: run_place(host, place, math.inf)) for place in load_places()),
    ]
    if purifier.CONFIG.exists():
        entries.append(("aria: controlla e avvisa quando peggiora", lambda: watch_air(host, math.inf, False)))
        entries.append(("aria: lo specchio resta del colore dell'aria", lambda: run_air(host, math.inf, False)))
        entries.append(("aria: prova di tutta la scala in 20 secondi", lambda: run_air(host, 0, True)))
    if HERE:
        here = read_here(HERE)
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
    storm.add_argument("name", nargs="?", default=next(iter(REAL_STORMS)), choices=[*REAL_STORMS, *STORMS])
    storm.add_argument("--seconds", type=float, help="default: endless with real sounds, 60 with synthetic ones")
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
    planes.add_argument("--qui", help='latitude,longitude of the house (default: "home" in tools/local.json)')
    planes.add_argument("--km", type=float, default=15)
    planes.add_argument("--seconds", type=float, default=math.inf)
    sounds = scenes.add_parser("suoni")
    sounds.add_argument("key", nargs="?", help="a key from sounds.json; without it, a menu")
    sounds.add_argument("--seconds", type=float, default=LISTEN_S)
    sounds.add_argument("--anticipo", type=float, default=SOUND_LEAD_S,
                        help="seconds the Mac needs to start the sound; raise it if the light comes before the thunder")
    air = scenes.add_parser("aria")
    air.add_argument("--prova", action="store_true", help="sweep the whole scale instead of reading the purifier")
    air.add_argument("--sempre", action="store_true", help="keep the mirror the colour of the air instead of only warning")
    air.add_argument("--anche-di-notte", action="store_true", help=f"blink also between {QUIET_HOURS[0]}:00 and {QUIET_HOURS[1]}:00")
    air.add_argument("--seconds", type=float, default=math.inf)
    spot = scenes.add_parser("luogo")
    spot.add_argument("name", nargs="?", default="", help="any place name; without it, the place picked on the globe")
    spot.add_argument("--alba", action="store_const", dest="replay", const="alba", default="")
    spot.add_argument("--tramonto", action="store_const", dest="replay", const="tramonto")
    spot.add_argument("--seconds", type=float, default=math.inf)
    sun = scenes.add_parser("sole")
    sun.add_argument("--replay", action="store_true", help="the last six hours in two minutes")
    sun.add_argument("--seconds", type=float, default=math.inf)
    sleep = scenes.add_parser("sonno")
    sleep.add_argument("--minuti", type=float, default=SLEEP_MINUTES)
    sleep.add_argument("--suono", default=SLEEP_SOUND, help="a key from sounds.json")
    scenes.add_parser("casa")
    scenes.add_parser("check")
    args = parser.parse_args()
    # Stop cleanly on Ctrl-C and on terminate, even when started with Ctrl-C ignored (a shell's background job).
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    sys.stdout.reconfigure(line_buffering=True)

    try:
        if args.scene == "temporale":
            if args.name in REAL_STORMS:
                run_real_storm(args.host, args.name, args.seconds or math.inf, args.anticipo)
            else:
                run_storm(args.host, args.name, args.seconds or 60, args.muto, args.anticipo)
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
            if not args.qui and not HERE:
                parser.error('aerei: serve --qui LAT,LON oppure "home": "LAT,LON" in tools/local.json')
            run_planes(args.host, read_here(args.qui or HERE), args.km, args.seconds)
        elif args.scene == "suoni" and args.key:
            by_key = {entry.key: entry for entry in load_sounds()}
            if args.key not in by_key:
                parser.error(f"suoni: uno tra {', '.join(by_key)}")
            listen(args.host, by_key[args.key], args.seconds, args.anticipo)
        elif args.scene == "suoni":
            sounds_menu(args.host, args.seconds, args.anticipo)
        elif args.scene == "aria":
            if args.prova or args.sempre:
                run_air(args.host, args.seconds, args.prova)
            else:
                watch_air(args.host, args.seconds, args.anche_di_notte)
        elif args.scene == "luogo":
            run_spot(args.host, args.name, args.seconds, args.replay)
        elif args.scene == "sole":
            run_sun(args.host, args.seconds, args.replay)
        elif args.scene == "sonno":
            run_sleep(args.host, args.minuti, args.suono)
        elif args.scene == "casa":
            run_home(args.host)
        elif args.scene == "check":
            check()
        else:
            menu(args.host)
    except KeyboardInterrupt:
        print("\nfermata")


if __name__ == "__main__":
    main()
