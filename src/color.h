// Pure colour/config logic: no Arduino headers, so it can be tested on the Mac.
#pragma once
#include <stdint.h>
#include <stdlib.h>
#include <math.h>

static const int PWM_MAX = 1023;  // analogWriteRange
static const float GAMMA = 2.2f;  // perceived brightness -> PWM duty

struct Rgb { float r, g, b; };  // 0..1 each

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

inline int toDuty(float level) {
  if (level <= 0) return 0;
  if (level >= 1) return PWM_MAX;
  return (int)lroundf(powf(level, GAMMA) * PWM_MAX);
}

// "12,4,14" -> pins[3]; only GPIOs safe to drive on an ESP8266 strip board.
inline bool parsePins(const char *s, int out[3]) {
  static const int allowed[] = {4, 5, 12, 13, 14, 15};
  for (int i = 0; i < 3; i++) {
    char *end;
    long p = strtol(s, &end, 10);
    if (end == s) return false;
    bool ok = false;
    for (int a : allowed) ok |= (p == a);
    if (!ok) return false;
    for (int j = 0; j < i; j++) if (out[j] == p) return false;  // no duplicates
    out[i] = (int)p;
    s = end;
    if (i < 2) { if (*s != ',') return false; s++; }
  }
  return *s == '\0';
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
