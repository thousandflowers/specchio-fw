// Run: c++ -std=c++17 -Isrc test/test_color.cpp -o /tmp/t && /tmp/t
#include <cassert>
#include <cstdio>
#include "color.h"

static bool near(float a, float b) { return fabsf(a - b) < 0.01f; }

int main() {
  Rgb red = hsvToRgb(0, 100, 100);
  assert(near(red.r, 1) && near(red.g, 0) && near(red.b, 0));
  Rgb blue = hsvToRgb(240, 100, 100);
  assert(near(blue.r, 0) && near(blue.g, 0) && near(blue.b, 1));
  Rgb white = hsvToRgb(123, 0, 50);
  assert(near(white.r, 0.5f) && near(white.g, 0.5f) && near(white.b, 0.5f));
  Rgb wrap = hsvToRgb(360, 100, 100);
  assert(near(wrap.r, 1) && near(wrap.g, 0));

  assert(toDuty(0) == 0 && toDuty(1) == PWM_MAX && toDuty(-1) == 0 && toDuty(2) == PWM_MAX);
  assert(toDuty(0.5f) > 150 && toDuty(0.5f) < 230);  // half lightness = ~18% duty
  assert(toDuty(0.01f) >= 1);                         // 1% must still emit light
  assert(toDuty(0.05f) > toDuty(0.02f));              // low end keeps distinct steps
  for (int i = 1; i <= 100; i++) assert(toDuty(i / 100.0f) >= toDuty((i - 1) / 100.0f));

  assert(clampf(5, 0, 10) == 5 && clampf(-1, 0, 10) == 0 && clampf(11, 0, 10) == 10);
  assert(clampf(NAN, 1, 100) == 1);                   // "nan" from the URL must not get through
  assert(clampf(INFINITY, 0, 360) == 360 && clampf(-INFINITY, 0, 360) == 0);

  int p[3];
  assert(parsePins("12,4,14", p) && p[0] == 12 && p[1] == 4 && p[2] == 14);
  assert(!parsePins("13,5,1", p) && p[0] == 12 && p[1] == 4 && p[2] == 14);  // bad input leaves out untouched
  assert(!parsePins("12,4", p));
  assert(!parsePins("12,4,4", p));     // duplicate
  assert(!parsePins("12,4,1", p));     // GPIO1 = TX, not allowed
  assert(!parsePins("12,4,14x", p));

  float h, s, v;
  assert(hexToHsv("00ff00", h, s, v) && near(h, 120) && near(s, 100) && near(v, 100));
  assert(hexToHsv("000000", h, s, v) && near(v, 0));
  assert(!hexToHsv("fff", h, s, v) && !hexToHsv("zzzzzz", h, s, v));

  puts("color tests ok");
}
