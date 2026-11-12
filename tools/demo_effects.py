#!/usr/bin/env python3
"""Preview candidate light effects on the strip, driven live from the Mac.

    demo_effects.py 192.0.2.66            # play every effect, announcing each name
    demo_effects.py 192.0.2.66 candela    # play one

Each effect is a function of time returning hue (0-360), saturation (0-1) and
brightness (0-1). The same functions are the reference for porting an effect
into the firmware. Frames go to /api/raw, which bypasses HomeKit and flash.
"""
import argparse
import colorsys
import math
import random
import subprocess
import time
from typing import Callable, NamedTuple

PWM_MAX = 1023
FRAME_S = 0.06
HTTP_TIMEOUT_S = 2
WARM_HUE = 30


class Hsv(NamedTuple):
    hue: float
    saturation: float
    value: float


class Effect(NamedTuple):
    key: str
    spoken_name: str
    seconds: float
    frame: Callable[[float, float], Hsv]  # (elapsed, total) -> colour


def wave(elapsed: float, period: float) -> float:
    """Smooth 0..1..0 oscillation."""
    return 0.5 - 0.5 * math.cos(2 * math.pi * elapsed / period)


def breathe(elapsed: float, _total: float) -> Hsv:
    return Hsv(WARM_HUE, 0.6, 0.08 + 0.92 * wave(elapsed, 4.0))


def candle(_elapsed: float, _total: float) -> Hsv:
    return Hsv(WARM_HUE - 8 + random.uniform(-4, 4), 0.85, random.uniform(0.35, 0.8))


def rainbow(elapsed: float, total: float) -> Hsv:
    return Hsv(360 * elapsed / total, 1.0, 0.8)


def sunrise(elapsed: float, total: float) -> Hsv:
    progress = elapsed / total
    return Hsv(5 + 30 * progress, 1.0 - 0.5 * progress, 0.02 + 0.98 * progress ** 2)


def sea(elapsed: float, _total: float) -> Hsv:
    return Hsv(185 + 40 * wave(elapsed, 5.0), 0.9, 0.45 + 0.35 * wave(elapsed, 3.1))


def sunset(elapsed: float, _total: float) -> Hsv:
    return Hsv((330 + 50 * wave(elapsed, 6.0)) % 360, 0.85, 0.7)


def heartbeat(elapsed: float, _total: float) -> Hsv:
    beat = elapsed % 1.2
    pulse = max(math.exp(-((beat - 0.1) / 0.06) ** 2), 0.7 * math.exp(-((beat - 0.4) / 0.07) ** 2))
    return Hsv(355, 1.0, 0.06 + 0.94 * pulse)


def flashes(hue: float, count: int) -> Callable[[float, float], Hsv]:
    """Short attention flashes in one colour, then dim: an event, not a mood."""
    def frame(elapsed: float, _total: float) -> Hsv:
        is_lit = elapsed % 0.6 < 0.25 and elapsed < 0.6 * count
        return Hsv(hue, 0.9, 1.0 if is_lit else 0.12)
    return frame


EFFECTS = (
    Effect("respiro", "Respiro", 9, breathe),
    Effect("candela", "Candela", 8, candle),
    Effect("arcobaleno", "Arcobaleno", 10, rainbow),
    Effect("alba", "Alba", 10, sunrise),
    Effect("mare", "Mare", 9, sea),
    Effect("tramonto", "Tramonto", 9, sunset),
    Effect("battito", "Battito", 7, heartbeat),
    Effect("notifica", "Notifica", 3, flashes(200, 2)),
    Effect("claude", "Claude ha finito", 2.4, flashes(25, 3)),
)


def to_duty(level: float) -> int:
    """Perceived lightness 0..1 -> PWM duty, CIE 1931 (same curve as color.h)."""
    if level <= 0:
        return 0
    lightness = min(level, 1.0) * 100
    luminance = lightness / 903.3 if lightness <= 8 else ((lightness + 16) / 116) ** 3
    return max(1, round(luminance * PWM_MAX))


def lan_get(host: str, path: str) -> bytes | None:
    """GET from a device on the home network, None when it does not answer.

    Goes through Apple's own curl on purpose: macOS lets Python reach local devices only when the app that
    started it was granted "Local Network" access. From a plain Terminal window the connection fails with
    errno 65 (No route to host) although the device is there, and curl is always allowed (measured 2026-11-12).
    """
    result = subprocess.run(["/usr/bin/curl", "-s", "-f", "-m", str(HTTP_TIMEOUT_S), f"http://{host}{path}"],
                            capture_output=True, check=False)
    return result.stdout if result.returncode == 0 else None


def send(host: str, colour: Hsv) -> bool:
    red, green, blue = colorsys.hsv_to_rgb(colour.hue % 360 / 360, colour.saturation, colour.value)
    return lan_get(host, f"/api/raw?r={to_duty(red)}&g={to_duty(green)}&b={to_duty(blue)}") is not None


def play(host: str, effect: Effect, should_speak: bool) -> tuple[int, int]:
    if should_speak:
        subprocess.run(["say", effect.spoken_name], check=False)
    sent = failed = 0
    start = time.monotonic()
    while (elapsed := time.monotonic() - start) < effect.seconds:
        if send(host, effect.frame(elapsed, effect.seconds)):
            sent += 1
        else:
            failed += 1
        time.sleep(max(0.0, FRAME_S - (time.monotonic() - start - elapsed)))
    return sent, failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host")
    parser.add_argument("effects", nargs="*", help=f"any of: {', '.join(e.key for e in EFFECTS)} (default: all)")
    parser.add_argument("--quiet", action="store_true", help="do not announce effect names aloud")
    args = parser.parse_args()

    by_key = {effect.key: effect for effect in EFFECTS}
    unknown = [key for key in args.effects if key not in by_key]
    if unknown:
        parser.error(f"unknown effect: {', '.join(unknown)}")
    chosen = [by_key[key] for key in args.effects] or list(EFFECTS)

    for effect in chosen:
        sent, failed = play(args.host, effect, not args.quiet)
        fps = sent / effect.seconds
        print(f"{effect.key}: {sent} frames ({fps:.0f}/s), {failed} lost")


if __name__ == "__main__":
    main()
