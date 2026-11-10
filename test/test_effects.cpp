// Run: c++ -std=c++11 -Wall -Isrc test/test_effects.cpp -o /tmp/t && /tmp/t
#include <cassert>
#include <cstdio>
#include "effects.h"

static bool near(float a, float b, float tolerance = 0.01f) { return fabsf(a - b) < tolerance; }

static void testSunrise() {
  Hsv none = {0, 0, 0};
  Hsv start = sunriseFrame(0, 1200, none), end = sunriseFrame(1200, 1200, none);
  assert(start.v <= 2.01f && near(start.s, 100));          // a dim red ember
  assert(near(end.v, 100) && near(end.h, 35) && near(end.s, 50));  // warm white, full
  for (int t = 1; t <= 1200; t++) {                         // never gets darker
    assert(sunriseFrame(t, 1200, none).v >= sunriseFrame(t - 1, 1200, none).v);
  }
  assert(sunriseFrame(300, 1200, none).v < 10);             // a quarter in: still barely there
  assert(near(sunriseFrame(9999, 1200, none).v, 100));      // past the end: clamped
}

static void testFocus() {
  Hsv lamp = {30, 60, 80};
  assert(near(focusFrame(0, 1500, lamp).h, 120));           // green
  assert(near(focusFrame(700, 1500, lamp).h, 64));          // yellow-ish just before half time
  assert(near(focusFrame(1500, 1500, lamp).h, 0));          // red
  assert(near(focusFrame(100, 1500, lamp).v, 80));          // keeps the lamp's brightness
  assert(focusFrame(752, 1500, lamp).v < 30);               // half-time dip is clearly visible
  assert(near(focusFrame(760, 1500, lamp).v, 80));          // and over within seconds
  Hsv off = {0, 0, 0};
  assert(near(focusFrame(10, 1500, off).v, FOCUS_LEVEL_IF_LAMP_OFF));  // lamp was off: still shows
}

static void testShimmer() {
  for (uint32_t t = 0; t < 5000; t += 7) {
    float g = shimmerGain(t, 0.3f);
    assert(g <= 1.0001f && g >= 0.6999f);                   // only dims, never past the amount
    assert(shimmerGain(t, 0) == 1);                         // off means untouched
  }
  // After 49 days of uptime it must still move from one frame to the next.
  const uint32_t late = 4200000000u;
  assert(!near(shimmerGain(late, 0.3f), shimmerGain(late + 100, 0.3f), 0.001f));
  assert(shimmerGain(1234, 5.0f) >= 0);                     // silly amount is clamped
}

static void testPulse() {
  Pulse n = notifyPulse({25, 90, 100}, 3);
  assert(pulseTotalMs(n) == 1800);
  assert(pulseIsLit(n, 0) && pulseIsLit(n, 249) && !pulseIsLit(n, 250) && !pulseIsLit(n, 599));
  assert(pulseIsLit(n, 600) && pulseIsLit(n, 1200) && !pulseIsLit(n, 1800));  // exactly three
  assert(notifyPulse({0, 0, 0}, 99).count == 10 && notifyPulse({0, 0, 0}, -4).count == 1);

  Pulse tick = tickPulse();
  assert(tick.colour.v == 0 && tick.restGain == 1.0f);      // blinks to black, lamp otherwise untouched
  assert(pulseTotalMs(tick) == 500);
}

static void testSmoothing() {
  assert(smoothToward(0, 1, 20, 0) == 1);                   // fade 0: immediate
  float level = 0;
  for (int ms = 0; ms < 250; ms += 20) {
    float next = smoothToward(level, 1, 20, 250);
    assert(next > level && next <= 1);                      // approaches, never overshoots
    level = next;
  }
  assert(level > 0.95f);                                    // ~done after one fade time
  for (int i = 0; i < 100; i++) level = smoothToward(level, 1, 20, 250);
  assert(level == 1);                                       // and actually lands
  assert(smoothToward(1, 0, 20, 250) < 1);                  // works downward too
}

static void testClock() {
  int last = -1;
  assert(!isNewHour(last, 14));                             // first reading after boot: no tick
  assert(!isNewHour(last, 14));
  assert(isNewHour(last, 15));
  assert(!isNewHour(last, 15));
  assert(isNewHour(last, 0) && last == 0);                  // midnight wraps
}

int main() {
  testSunrise();
  testFocus();
  testShimmer();
  testPulse();
  testSmoothing();
  testClock();
  puts("effects tests ok");
}
