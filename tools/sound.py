"""Synthesised noise for the mirror scenes, stdlib only, played by the Mac.

Colours of noise: white is a hiss, pink sounds like rain, brown like a far
waterfall. Thunder is brown noise under a decaying, rumbling envelope, with a
white crack on top when the strike is close.
"""
import array
import atexit
import math
import random
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Callable

RATE = 22050
PEAK = 0.9 * 32767
CROSSFADE_S = 1.0   # overlap between two passes of a loop: hides the seam
LOOP_S = 20.0
THUNDER_S = 7.0
CRACK_S = 0.3
NEAR = 0.4          # strikes closer than this get the crack
ENVELOPE_STEP_S = 0.05

_folder = Path(tempfile.mkdtemp(prefix="specchio-sound-"))
_players: list[subprocess.Popen] = []
_is_stopping = threading.Event()


def _white(count: int) -> list[float]:
    return [random.uniform(-1, 1) for _ in range(count)]


def _pink(count: int) -> list[float]:
    """Paul Kellet's economy filter: -3 dB per octave."""
    low = mid = high = 0.0
    samples = []
    for value in _white(count):
        low = 0.99765 * low + value * 0.0990460
        mid = 0.96300 * mid + value * 0.2965164
        high = 0.57000 * high + value * 1.0526913
        samples.append(low + mid + high + value * 0.1848)
    return samples


def _brown(count: int) -> list[float]:
    """Leaky integrator: -6 dB per octave."""
    level = 0.0
    samples = []
    for value in _white(count):
        level = (level + 0.02 * value) / 1.02
        samples.append(level)
    return samples


COLOURS: dict[str, Callable[[int], list[float]]] = {"white": _white, "pink": _pink, "brown": _brown}


def noise(colour: str, seconds: float) -> list[float]:
    return COLOURS[colour](int(seconds * RATE))


def thunder(distance: float, seconds: float = THUNDER_S) -> list[float]:
    """distance 0 = overhead (crack, then roar), 1 = far (a soft long rumble)."""
    rumble = _brown(int(seconds * RATE))
    peak = max(abs(value) for value in rumble) or 1.0
    swells = [(random.uniform(0.6, 3.0), random.uniform(0, math.tau)) for _ in range(3)]
    decay_s = seconds / (3.0 + 2.0 * (1 - distance))
    samples = []
    for index, value in enumerate(rumble):
        elapsed = index / RATE
        swell = sum(abs(math.sin(math.tau * rate * elapsed + phase)) for rate, phase in swells) / len(swells)
        envelope = min(1.0, elapsed / 0.08) * math.exp(-elapsed / decay_s) * (0.4 + 0.6 * swell)
        samples.append(value / peak * envelope)
    if distance < NEAR:
        strength = 1 - distance / NEAR
        for index in range(int(CRACK_S * RATE)):
            samples[index] += strength * random.uniform(-1, 1) * math.exp(-index / RATE / 0.07)
    return samples


def envelope(samples: list[float]) -> tuple[float, ...]:
    """Loudness over time, 0..1, one value every ENVELOPE_STEP_S: lets the light follow the sound."""
    window = int(ENVELOPE_STEP_S * RATE)
    levels = [max(map(abs, samples[start:start + window])) for start in range(0, len(samples), window)]
    peak = max(levels) or 1.0
    return tuple(level / peak for level in levels)


def save(samples: list[float], name: str, fade_s: float = 0.0) -> Path:
    """Normalise, fade both ends (equal power, so two overlapped copies keep the level) and write a WAV."""
    peak = max(abs(value) for value in samples) or 1.0
    fade = int(fade_s * RATE)
    frames = array.array("h")
    for index, value in enumerate(samples):
        edge = min(index, len(samples) - 1 - index)
        gain = math.sin(math.pi / 2 * edge / fade) if edge < fade else 1.0
        frames.append(int(value / peak * PEAK * gain))
    path = _folder / f"{name}.wav"
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(RATE)
        file.writeframes(frames.tobytes())
    return path


def play(path: Path, volume: float = 1.0) -> None:
    if _is_stopping.is_set():
        return
    _players[:] = [player for player in _players if player.poll() is None]
    _players.append(subprocess.Popen(["afplay", "-v", f"{volume:.2f}", str(path)]))


def loop(colour: str, volume: float) -> None:
    """Play a noise colour until stop(): each pass starts under the tail of the one before."""
    path = save(noise(colour, LOOP_S), f"loop-{colour}", CROSSFADE_S)

    def run() -> None:
        while not _is_stopping.is_set():
            play(path, volume)
            _is_stopping.wait(LOOP_S - CROSSFADE_S)

    threading.Thread(target=run, daemon=True).start()


def later(seconds: float, path: Path, volume: float) -> None:
    timer = threading.Timer(seconds, play, (path, volume))
    timer.daemon = True
    timer.start()


def stop() -> None:
    """Silence everything; start() makes the module usable again for the next scene."""
    _is_stopping.set()
    for player in _players:
        if player.poll() is None:
            player.terminate()


def start() -> None:
    _is_stopping.clear()


def _cleanup() -> None:
    stop()
    shutil.rmtree(_folder, ignore_errors=True)


atexit.register(_cleanup)


def demo() -> None:
    """Self-check: every colour has the right length and is not silent; near thunder starts louder than far."""
    for colour in COLOURS:
        samples = noise(colour, 0.5)
        assert len(samples) == RATE // 2 and max(map(abs, samples)) > 0, colour
    head = int(CRACK_S * RATE)
    loudness = {distance: sum(abs(v) for v in thunder(distance, 2.0)[:head]) for distance in (0.0, 1.0)}
    assert loudness[0.0] > loudness[1.0], loudness
    shape = envelope(thunder(0.0, 2.0))
    assert abs(len(shape) * ENVELOPE_STEP_S - 2.0) < 0.1 and max(shape) == 1.0 and shape[-2] < 0.3, "loud at the crack, fading at the end"
    path = save(noise("pink", 1.0), "check", 0.2)
    with wave.open(str(path)) as file:
        assert file.getnframes() == RATE and file.getframerate() == RATE
        frames = array.array("h", file.readframes(RATE))
    assert frames[0] == 0 and max(frames) > 20000, "faded edge and normalised peak"
    print("sound ok")


if __name__ == "__main__":
    demo()
