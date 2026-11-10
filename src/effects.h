// Light effects as pure functions of time: no Arduino headers, tested on the
// Mac (test/test_effects.cpp). Looks can be previewed and tuned from the Mac
// with tools/demo_effects.py before being ported here.
#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>
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

// ---------- wake-up: sunrise before the alarm, insistence after it ----------
// The phone tells the strip when its next alarm is; from then on the strip
// needs no network. Timeline around the alarm time T:
//   T-lead .. T          sunrise, ends at full warm light exactly at T
//   T .. T+insistAfter   steady full light: the gentle part
//   then, for WAKE_INSIST_S: pulses that grow faster and deeper
//   after that           steady full light again, and the wake-up is over
enum WakePhase { WAKE_WAITING, WAKE_SUNRISE, WAKE_HOLD, WAKE_INSIST, WAKE_OVER };

static const int MIN_WAKE_LEAD_MIN = 1;
static const int MAX_WAKE_LEAD_MIN = 60;
static const int MAX_WAKE_INSIST_MIN = 60;

static const int32_t WAKE_INSIST_S = 20 * 60;
static const Hsv WAKE_FULL_LIGHT = {35, 50, 100};  // where the sunrise ends
// Never above 2 flashes a second: flicker from about 3 Hz up is the range that
// can trigger seizures in photosensitive people.
static const float WAKE_PULSE_HZ_START = 0.25f;
static const float WAKE_PULSE_HZ_END = 2.0f;
static const float WAKE_PULSE_DEPTH_START = 0.3f;
static const float WAKE_PULSE_DEPTH_END = 1.0f;

// insistAfterS <= 0 means "never insist": the light just stays on.
inline WakePhase wakePhase(int32_t secondsFromAlarm, int32_t leadS, int32_t insistAfterS) {
  if (secondsFromAlarm < -leadS) return WAKE_WAITING;
  if (secondsFromAlarm < 0) return WAKE_SUNRISE;
  if (insistAfterS <= 0) return secondsFromAlarm < WAKE_INSIST_S ? WAKE_HOLD : WAKE_OVER;
  if (secondsFromAlarm < insistAfterS) return WAKE_HOLD;
  if (secondsFromAlarm < insistAfterS + WAKE_INSIST_S) return WAKE_INSIST;
  return WAKE_OVER;
}

// Pulses that start slow and shallow and end fast and to black. The phase is
// the integral of a linearly rising frequency, so the rhythm speeds up
// smoothly instead of jumping. The colour drifts from warm to plain white.
inline Hsv wakeInsistFrame(float secondsIn, float totalS) {
  float p = clampf(secondsIn / totalS, 0, 1);
  float t = p * totalS;
  float cycles = WAKE_PULSE_HZ_START * t + (WAKE_PULSE_HZ_END - WAKE_PULSE_HZ_START) * t * t / (2 * totalS);
  float depth = WAKE_PULSE_DEPTH_START + (WAKE_PULSE_DEPTH_END - WAKE_PULSE_DEPTH_START) * p;
  float dip = 0.5f - 0.5f * cosf(2 * PI_F * (cycles - floorf(cycles)));  // 0 at the top of each pulse
  return {WAKE_FULL_LIGHT.h, WAKE_FULL_LIGHT.s * (1 - p), 100 * (1 - depth * dip)};
}

// Seconds from now until the next time the wall clock reads hour:minute.
// ponytail: plain 24 h arithmetic, so on the two nights a year the clocks
// change the light is an hour off; the phone re-sends the time every evening.
inline int32_t secondsUntilClock(int nowHour, int nowMinute, int nowSecond, int hour, int minute) {
  int32_t delta = (hour * 3600 + minute * 60) - (nowHour * 3600 + nowMinute * 60 + nowSecond);
  return delta > 0 ? delta : delta + 86400;
}

// "07:30" -> hour, minute. False unless it is exactly HH:MM in range.
inline bool parseClock(const char *s, int &hour, int &minute) {
  if (strlen(s) != 5 || s[2] != ':') return false;
  for (int i = 0; i < 5; i++) if (i != 2 && (s[i] < '0' || s[i] > '9')) return false;
  int h = (s[0] - '0') * 10 + (s[1] - '0'), m = (s[3] - '0') * 10 + (s[4] - '0');
  if (h > 23 || m > 59) return false;
  hour = h;
  minute = m;
  return true;
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
