"""Sound for the mirror scenes, played by the Mac: synthesised noise (stdlib only)
and real recordings (downloaded once, decoded with ffmpeg).

Colours of noise: white is a hiss, pink sounds like rain, brown like a far
waterfall. Thunder is brown noise under a decaying, rumbling envelope, with a
white crack on top when the strike is close.
"""
import array
import atexit
import hashlib
import json
import math
import random
import re
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Callable, NamedTuple
from urllib.request import Request, urlopen

RATE = 22050
PEAK = 0.9 * 32767
CROSSFADE_S = 1.0   # overlap between two passes of a loop: hides the seam
LOOP_S = 20.0
THUNDER_S = 7.0
CRACK_S = 0.3
NEAR = 0.4          # strikes closer than this get the crack
ENVELOPE_STEP_S = 0.05
CACHE = Path.home() / "Library" / "Caches" / "specchio-scenes"   # recordings, decoded once
BASS_HZ = 200           # thunder lives below this; rain mostly above
BASS_RATE = 1000
BED_PERCENTILE = 0.2    # the quietest fifth of a recording is its steady bed (rain, hum)
BED_MARGIN = 2.0        # the low band must be this many times louder than the bed to count as thunder
DOWNLOAD_TIMEOUT_S = 180
USER_AGENT = "specchio-scenes/1.0 (hobby light script, one download per file)"   # Wikimedia refuses anonymous agents

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


def play(path: Path, volume: float = 1.0, seconds: float | None = None) -> None:
    if _is_stopping.is_set():
        return
    limit = ["-t", f"{seconds:.2f}"] if seconds else []
    _players[:] = [player for player in _players if player.poll() is None]
    _players.append(subprocess.Popen(["afplay", "-v", f"{volume:.2f}", *limit, str(path)]))


# ---------- real recordings ----------


class Recording(NamedTuple):
    path: Path                    # mono WAV, peak just under full scale
    seconds: float
    thunder: tuple[float, ...]    # loudness of the low band above the steady bed, 0..1, every ENVELOPE_STEP_S


def _ffmpeg(*arguments: str) -> subprocess.CompletedProcess:
    if not shutil.which("ffmpeg"):
        raise OSError("serve ffmpeg per le registrazioni: brew install ffmpeg")
    return subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-y", *arguments], capture_output=True, check=True)


def thunder_shape(bass: array.array) -> list[float]:
    """Loudness of the low band over time with the steady bed removed: what the lightning follows."""
    window = int(ENVELOPE_STEP_S * BASS_RATE)
    levels = [max(max(chunk), -min(chunk)) for chunk in (bass[at:at + window] for at in range(0, len(bass), window))]
    bed = sorted(levels)[int(len(levels) * BED_PERCENTILE)] * BED_MARGIN
    top = max(levels)
    if top <= bed:
        return [0.0] * len(levels)
    return [round(max(0.0, (level - bed) / (top - bed)), 3) for level in levels]


def cache_stem(url: str) -> Path:
    return CACHE / hashlib.sha1(url.encode()).hexdigest()[:12]


def prepare(source: Path, stem: Path) -> None:
    """Any audio file -> normalised mono WAV plus its thunder shape, next to each other in the cache."""
    wav, shape = stem.with_suffix(".wav"), stem.with_suffix(".json")
    report = _ffmpeg("-i", str(source), "-af", "volumedetect", "-f", "null", "-").stderr.decode(errors="replace")
    peak = re.search(r"max_volume: (-?[\d.]+) dB", report)
    gain = -float(peak.group(1)) - 1 if peak else 0.0
    _ffmpeg("-i", str(source), "-ac", "1", "-ar", str(RATE), "-af", f"volume={gain:.1f}dB", str(wav))
    bass = _ffmpeg("-i", str(wav), "-af", f"lowpass=f={BASS_HZ}", "-ar", str(BASS_RATE), "-f", "s16le", "-").stdout
    shape.write_text(json.dumps(thunder_shape(array.array("h", bass))))


def recording(url: str) -> Recording:
    """Download once, then always from the cache."""
    CACHE.mkdir(parents=True, exist_ok=True)
    stem = cache_stem(url)
    wav, shape = stem.with_suffix(".wav"), stem.with_suffix(".json")
    if not (wav.exists() and shape.exists()):
        source = stem.with_suffix(".src")
        with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=DOWNLOAD_TIMEOUT_S) as reply:
            source.write_bytes(reply.read())
        prepare(source, stem)
        source.unlink()
    with wave.open(str(wav)) as file:
        seconds = file.getnframes() / file.getframerate()
    return Recording(wav, seconds, tuple(json.loads(shape.read_text())))


def busiest(shape: tuple[float, ...], seconds: float) -> float:
    """Start (in seconds) of the stretch of that length with the most thunder in it."""
    span = int(seconds / ENVELOPE_STEP_S)
    if len(shape) <= span:
        return 0.0
    best = total = sum(shape[:span])
    start = 0
    for index in range(span, len(shape)):
        total += shape[index] - shape[index - span]
        if total > best:
            best, start = total, index - span + 1
    return start * ENVELOPE_STEP_S


def faded(source: Recording) -> Path:
    """A copy with both ends faded (equal power), ready for repeat()."""
    path = _folder / f"faded-{source.path.stem}.wav"
    if not path.exists():
        fades = (f"afade=t=in:d={CROSSFADE_S}:curve=qsin,"
                 f"afade=t=out:st={source.seconds - CROSSFADE_S:.2f}:d={CROSSFADE_S}:curve=qsin")
        _ffmpeg("-i", str(source.path), "-af", fades, str(path))
    return path


def excerpt(source: Recording, start: float, seconds: float) -> Path:
    path = _folder / f"excerpt-{source.path.stem}.wav"
    _ffmpeg("-ss", f"{start:.2f}", "-t", f"{seconds:.2f}", "-i", str(source.path), str(path))
    return path


def repeat(path: Path, seconds: float, volume: float) -> None:
    """Play a file with faded ends until stop(): each pass starts under the tail of the one before."""
    def run() -> None:
        while not _is_stopping.is_set():
            play(path, volume)
            _is_stopping.wait(seconds - CROSSFADE_S)

    threading.Thread(target=run, daemon=True).start()


def loop(colour: str, volume: float) -> None:
    repeat(save(noise(colour, LOOP_S), f"loop-{colour}", CROSSFADE_S), LOOP_S, volume)


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
    quiet, loud = [100, -100] * 25, [20000, -20000] * 25   # 50 ms each at BASS_RATE
    shape = thunder_shape(array.array("h", quiet * 8 + loud + quiet))
    assert shape[8] == 1.0 and max(shape[:8] + shape[9:]) == 0.0, "only the loud window stands above the bed"
    assert busiest((0, 0, 0, 0, 1, 1, 0, 0), 2 * ENVELOPE_STEP_S) == 4 * ENVELOPE_STEP_S
    path = save(noise("pink", 1.0), "check", 0.2)
    with wave.open(str(path)) as file:
        assert file.getnframes() == RATE and file.getframerate() == RATE
        frames = array.array("h", file.readframes(RATE))
    assert frames[0] == 0 and max(frames) > 20000, "faded edge and normalised peak"
    print("sound ok")


if __name__ == "__main__":
    demo()
