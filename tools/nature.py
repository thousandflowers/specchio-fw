"""Pure maths for the living scenes: no network, no lamp.

Where the sun is over a place, what colour its sky has, how the Sun's own
activity looks, and a breath-paced light that fades to nothing for falling
asleep. Everything here is a function of numbers, checked by demo().
"""
import math

from demo_effects import Hsv

# ---------- the sky over a place ----------

J2000_EPOCH_S = 946728000.0   # 2000-01-01 12:00 UTC

# Sun height above the horizon (degrees) -> colour of the sky. Between gold and
# blue the path goes through white (low saturation), never through green.
SKY = (
    (-18.0, Hsv(240, 0.90, 0.03)),   # night
    (-10.0, Hsv(250, 0.85, 0.06)),   # first light
    (-5.0, Hsv(285, 0.70, 0.14)),    # violet twilight
    (-2.0, Hsv(340, 0.80, 0.30)),    # pink
    (0.0, Hsv(12, 0.95, 0.50)),      # the sun on the horizon: red
    (3.0, Hsv(28, 0.85, 0.70)),      # orange
    (8.0, Hsv(42, 0.50, 0.90)),      # gold
    (14.0, Hsv(45, 0.08, 1.00)),     # white
    (22.0, Hsv(205, 0.14, 1.00)),
    (90.0, Hsv(205, 0.32, 1.00)),    # high sun: blue
)
CLOUD_GREYS = 0.7     # a fully overcast sky loses this share of its colour
CLOUD_DIMS = 0.35     # and this share of its light
THUNDER_CODES = frozenset({95, 96, 99})   # WMO weather codes for a thunderstorm


def sun_elevation(latitude: float, longitude: float, epoch_s: float) -> float:
    """Degrees of the sun above the horizon (negative below), good to about half a degree."""
    days = (epoch_s - J2000_EPOCH_S) / 86400
    anomaly = math.radians((357.528 + 0.9856003 * days) % 360)
    ecliptic = math.radians((280.460 + 0.9856474 * days) % 360 + 1.915 * math.sin(anomaly) + 0.020 * math.sin(2 * anomaly))
    tilt = math.radians(23.439 - 0.0000004 * days)
    declination = math.asin(math.sin(tilt) * math.sin(ecliptic))
    right_ascension = math.atan2(math.cos(tilt) * math.sin(ecliptic), math.cos(ecliptic))
    sidereal_hours = (18.697374558 + 24.06570982441908 * days) % 24
    hour_angle = math.radians(sidereal_hours * 15 + longitude) - right_ascension
    lat = math.radians(latitude)
    return math.degrees(math.asin(
        math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(declination) * math.cos(hour_angle)))


def blend(first: Hsv, second: Hsv, share: float) -> Hsv:
    """From first (share 0) to second (share 1), hue by the shorter way round the wheel."""
    turn = (second.hue - first.hue + 180) % 360 - 180
    return Hsv((first.hue + turn * share) % 360,
               first.saturation + (second.saturation - first.saturation) * share,
               first.value + (second.value - first.value) * share)


def sky_colour(elevation: float, cloud: float = 0.0) -> Hsv:
    """Colour of the sky for a sun height; cloud 0..1 greys and dims it."""
    low, high = SKY[0], SKY[-1]
    if elevation <= low[0]:
        clear = low[1]
    elif elevation >= high[0]:
        clear = high[1]
    else:
        clear = next(blend(colour, after, (elevation - at) / (after_at - at))
                     for (at, colour), (after_at, after) in zip(SKY, SKY[1:]) if elevation <= after_at)
    return Hsv(clear.hue, clear.saturation * (1 - CLOUD_GREYS * cloud), clear.value * (1 - CLOUD_DIMS * cloud))


def weather_wobble(elapsed: float, wind_kmh: float, rain_mm: float) -> float:
    """Brightness factor around 1: slow sway from the wind, a fine tremble from the rain."""
    sway = min(0.3, wind_kmh / 100) * (
        math.sin(1.45 * elapsed) + math.sin(3.83 * elapsed + 1) + math.sin(8.61 * elapsed + 2)) / 3
    tremble = 0.08 * min(1.0, rain_mm / 2) * math.sin(44 * elapsed) * math.sin(5.7 * elapsed)
    return 1 + sway + tremble


# ---------- the Sun itself ----------

FLARE_CLASSES = ((1e-4, "X"), (1e-5, "M"), (1e-6, "C"), (1e-7, "B"), (1e-8, "A"))   # X-ray flux in W/m², GOES long band


def flare_level(flux: float) -> float:
    """0 = quiet Sun (class A-B), about 0.7 = class M, 1 = class X."""
    return min(1.0, max(0.0, (math.log10(max(flux, 1e-9)) + 7.5) / 3.5))


def flare_class(flux: float) -> str:
    for floor, letter in FLARE_CLASSES:
        if flux >= floor:
            return f"{letter}{flux / floor:.1f}"
    return "A0.0"


def sun_colour(level: float, elapsed: float) -> Hsv:
    """A simmering amber that turns white-hot and restless as the flux climbs."""
    pace = 0.5 + 3.5 * level
    simmer = (math.sin(pace * elapsed) + math.sin(2.3 * pace * elapsed + 1) + math.sin(5.1 * pace * elapsed + 2)) / 3
    value = (0.22 + 0.78 * level) * (1 + (0.06 + 0.22 * level) * simmer)
    return Hsv(28 + 18 * level, 1 - 0.75 * level ** 2, min(1.0, max(0.0, value)))


# ---------- falling asleep ----------

BREATH_START_S = 6.0      # ten breaths a minute at the start
BREATH_END_S = 10.0       # six a minute once settled: the pace the small sleep studies used
BREATH_SLOWING_S = 480.0  # reached after eight minutes
INHALE_SHARE = 0.4        # the exhale is the longer part
SLEEP_HUE = 14            # deep amber: little of the blue that keeps the body awake
SLEEP_PEAK = 0.12         # dim on purpose: brightness matters more than colour
SLEEP_FLOOR = 0.25        # the light never quite goes out between breaths, until the end


def breaths_so_far(elapsed: float) -> float:
    """How many breaths the guide has done: the period grows evenly from start to end pace."""
    slowing = min(elapsed, BREATH_SLOWING_S)
    rate = (BREATH_END_S - BREATH_START_S) / BREATH_SLOWING_S   # seconds of period gained per second
    during = math.log((BREATH_START_S + rate * slowing) / BREATH_START_S) / rate
    return during + max(0.0, elapsed - BREATH_SLOWING_S) / BREATH_END_S


def breath_glow(breaths: float) -> float:
    """0..1 within one breath: rises while breathing in, falls (longer) while breathing out."""
    part = breaths % 1
    if part < INHALE_SHARE:
        return (1 - math.cos(math.pi * part / INHALE_SHARE)) / 2
    return (1 + math.cos(math.pi * (part - INHALE_SHARE) / (1 - INHALE_SHARE))) / 2


def sleep_light(elapsed: float, total: float) -> Hsv:
    """Breathe with it; it dims the whole time and is dark at the end."""
    fade = max(0.0, 1 - elapsed / total) ** 1.5
    return Hsv(SLEEP_HUE, 1.0, SLEEP_PEAK * fade * (SLEEP_FLOOR + (1 - SLEEP_FLOOR) * breath_glow(breaths_so_far(elapsed))))


def demo() -> None:
    equinox_noon = 1773921600.0    # 2026-03-19 12:00 UTC, a day before the equinox
    assert sun_elevation(0, 0, equinox_noon) > 85 and sun_elevation(0, 180, equinox_noon) < -85
    solstice_noon = 1782042720.0   # 2026-06-21 11:52 UTC, solar noon at 2.35 E
    assert abs(sun_elevation(48.86, 2.35, solstice_noon) - 64.6) < 1.0, sun_elevation(48.86, 2.35, solstice_noon)
    assert sky_colour(-30) == SKY[0][1] and sky_colour(90) == SKY[-1][1]
    assert sky_colour(0).hue == 12 and 12 < sky_colour(1.5).hue < 28
    assert all(not 70 < sky_colour(e / 4).hue < 170 or sky_colour(e / 4).saturation < 0.2 for e in range(-80, 360)), "no green sky"
    assert sky_colour(40, 1.0).saturation < sky_colour(40).saturation and sky_colour(40, 1.0).value < 1
    assert blend(Hsv(350, 1, 1), Hsv(10, 1, 1), 0.5).hue == 0, "hue takes the short way"
    assert weather_wobble(3.0, 0, 0) == 1 and abs(weather_wobble(3.0, 200, 10) - 1) <= 0.38

    assert flare_level(1e-9) == 0 and flare_level(1e-4) == 1 and 0.6 < flare_level(1e-5) < 0.8
    assert (flare_class(2.4e-6), flare_class(1.1e-4), flare_class(3e-9)) == ("C2.4", "X1.1", "A0.0")
    assert sun_colour(0, 1).value < 0.3 and sun_colour(1, 1).saturation == 0.25

    assert breaths_so_far(0) == 0 and 9 < breaths_so_far(60) < 10, "about ten breaths in the first minute"
    assert abs((breaths_so_far(900) - breaths_so_far(840)) - 6) < 0.01, "six a minute once settled"
    assert breath_glow(0) == 0 and breath_glow(INHALE_SHARE) == 1 and abs(breath_glow(0.7) - 0.5) < 1e-9
    assert sleep_light(1200, 1200).value == 0 and 0 < sleep_light(2.4, 1200).value <= SLEEP_PEAK
    print("nature ok")


if __name__ == "__main__":
    demo()
