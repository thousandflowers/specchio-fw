// Pure colour/config logic: no Arduino headers, so it can be tested on the Mac.
#pragma once
#include <stdint.h>
#include <stdlib.h>
#include <math.h>

static const int PWM_MAX = 1023;  // analogWriteRange

struct Rgb { float r, g, b; };  // 0..1 each

// Unlike Arduino's constrain() macro this also catches NaN, which atof()
// happily parses from "nan" and which would otherwise be saved to flash.
inline float clampf(float x, float lo, float hi) {
  if (!(x >= lo)) return lo;
  return x > hi ? hi : x;
}

// h 0..360, s 0..100, v 0..100 (HomeKit units)
inline Rgb hsvToRgb(float h, float s, float v) {
  s /= 100.0f; v /= 100.0f;
  h = fmodf(h, 360.0f); if (h < 0) h += 360.0f;
  float c = v * s, x = c * (1 - fabsf(fmodf(h / 60.0f, 2) - 1)), m = v - c;
  float r = 0, g = 0, b = 0;
  switch ((int)(h / 60.0f)) {
    case 0: r = c; g = x; break;
    case 1: r = x; g = c; break;
    case 2: g = c; b = x; break;
    case 3: g = x; b = c; break;
    case 4: r = x; b = c; break;
    default: r = c; b = x; break;
  }
  return {r + m, g + m, b + m};
}

// Perceived lightness 0..1 -> PWM duty, CIE 1931 L*. Unlike a plain 2.2 gamma
// it is linear near zero, so 1% brightness is still light instead of black.
inline int toDuty(float level) {
  if (level <= 0) return 0;
  if (level >= 1) return PWM_MAX;
  float lightness = level * 100.0f;
  float luminance = lightness <= 8.0f ? lightness / 903.3f
                                      : powf((lightness + 16.0f) / 116.0f, 3.0f);
  int duty = (int)lroundf(luminance * PWM_MAX);
  return duty < 1 ? 1 : duty;
}

// "12,4,14" -> pins[3]; only GPIOs safe to drive on an ESP8266 strip board.
// `out` is left untouched unless the whole string is valid.
inline bool parsePins(const char *s, int out[3]) {
  static const int allowed[] = {4, 5, 12, 13, 14, 15};
  int parsed[3];
  for (int i = 0; i < 3; i++) {
    char *end;
    long p = strtol(s, &end, 10);
    if (end == s) return false;
    bool ok = false;
    for (int a : allowed) ok |= (p == a);
    if (!ok) return false;
    for (int j = 0; j < i; j++) if (parsed[j] == p) return false;  // no duplicates
    parsed[i] = (int)p;
    s = end;
    if (i < 2) { if (*s != ',') return false; s++; }
  }
  if (*s != '\0') return false;
  for (int i = 0; i < 3; i++) out[i] = parsed[i];
  return true;
}

// "ff8000" -> h,s,v
inline bool hexToHsv(const char *hex, float &h, float &s, float &v) {
  char *end;
  long n = strtol(hex, &end, 16);
  if (end - hex != 6 || *end != '\0' || n < 0) return false;
  float r = ((n >> 16) & 255) / 255.0f, g = ((n >> 8) & 255) / 255.0f, b = (n & 255) / 255.0f;
  float mx = fmaxf(r, fmaxf(g, b)), mn = fminf(r, fminf(g, b)), d = mx - mn;
  h = 0;
  if (d > 0) {
    if (mx == r) h = 60 * fmodf((g - b) / d, 6);
    else if (mx == g) h = 60 * ((b - r) / d + 2);
    else h = 60 * ((r - g) / d + 4);
    if (h < 0) h += 360;
  }
  s = mx > 0 ? d / mx * 100 : 0;
  v = mx * 100;
  return true;
}
