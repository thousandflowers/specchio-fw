// Light effects as pure functions of time: no Arduino headers, tested on the
// Mac (test/test_effects.cpp). Looks can be previewed and tuned from the Mac
// with tools/demo_effects.py before being ported here.
#pragma once
#include <math.h>
#include <stdint.h>
#include "color.h"

struct Hsv { float h, s, v; };  // 0-360, 0-100, 0-100: HomeKit units

static const float MIN_EFFECT_MINUTES = 0.1f;
static const float MAX_EFFECT_MINUTES = 180.0f;

static const float PI_F = 3.14159265f;

// ---------- timed effects: frame(elapsedSec, totalSec, lightBefore) ----------

// Deep red ember to warm white. Brightness follows a square curve so the
// first minutes stay very dim, like a real dawn.
inline Hsv sunriseFrame(float elapsed, float total, const Hsv &) {
  float p = clampf(elapsed / total, 0, 1);
  return {5 + 30 * p, 100 - 50 * p, 2 + 98 * p * p};
}

// A timer you see without looking at it: green -> yellow -> red over the
// session, with one slow dip at half time.
static const float FOCUS_HALFWAY_DIP_S = 4.0f;
static const float FOCUS_HALFWAY_DIP_DEPTH = 0.7f;
static const float FOCUS_LEVEL_IF_LAMP_OFF = 60.0f;

inline Hsv focusFrame(float elapsed, float total, const Hsv &before) {
  float p = clampf(elapsed / total, 0, 1);
  float level = before.v > 0 ? before.v : FOCUS_LEVEL_IF_LAMP_OFF;
  float sinceHalf = elapsed - total / 2;
  if (sinceHalf >= 0 && sinceHalf < FOCUS_HALFWAY_DIP_S) {
    level *= 1 - FOCUS_HALFWAY_DIP_DEPTH * sinf(PI_F * sinceHalf / FOCUS_HALFWAY_DIP_S);
  }
  return {120 * (1 - p), 100, level};
}

// ---------- shimmer: the lamp is never perfectly still ----------
// Two sines with unrelated periods. Works on integer milliseconds so it does
// not go coarse after days of uptime, as a float seconds counter would.
static const uint32_t SHIMMER_SLOW_MS = 900;
static const uint32_t SHIMMER_FAST_MS = 370;

// amount 0..1 -> brightness gain in [1 - amount, 1]: it only ever dims, so it
// cannot clip at full brightness.
inline float shimmerGain(uint32_t nowMs, float amount) {
  float slow = sinf(2 * PI_F * (nowMs % SHIMMER_SLOW_MS) / SHIMMER_SLOW_MS);
  float fast = sinf(2 * PI_F * (nowMs % SHIMMER_FAST_MS) / SHIMMER_FAST_MS);
  float wobble = 0.5f + 0.5f * (0.6f * slow + 0.4f * fast);  // 0..1
  return 1 - clampf(amount, 0, 1) * wobble;
}

// ---------- pulses: short events drawn over whatever the lamp is doing ----------
struct Pulse {
  Hsv colour;       // shown during the "on" part
  uint8_t count;
  uint16_t onMs, offMs;
  float restGain;   // brightness of the underlying light during the "off" part
};

inline uint32_t pulseTotalMs(const Pulse &p) { return (uint32_t)p.count * (p.onMs + p.offMs); }

inline bool pulseIsLit(const Pulse &p, uint32_t elapsedMs) {
  return elapsedMs < pulseTotalMs(p) && elapsedMs % (p.onMs + p.offMs) < p.onMs;
}

// Coloured flashes with the lamp dimmed in between: "something happened".
inline Pulse notifyPulse(Hsv colour, int count) {
  if (count < 1) count = 1;
  if (count > 10) count = 10;
  return {colour, (uint8_t)count, 250, 350, 0.1f};
}

// Two brief blinks to black, lamp untouched otherwise: the tick of a clock.
inline Pulse tickPulse() { return {{0, 0, 0}, 2, 70, 180, 1.0f}; }

// ---------- output smoothing ----------
// Moves `current` toward `target`; a step change is ~98% done after fadeMs.
inline float smoothToward(float current, float target, uint32_t dtMs, uint16_t fadeMs) {
  if (fadeMs == 0) return target;
  float next = current + (target - current) * (1 - expf(-4.0f * dtMs / fadeMs));
  return fabsf(target - next) < 0.002f ? target : next;
}

// ---------- clock ----------
// True once per change of hour; never on the first reading after boot.
inline bool isNewHour(int &lastHour, int hour) {
  bool changed = lastHour >= 0 && hour != lastHour;
  lastHour = hour;
  return changed;
}
