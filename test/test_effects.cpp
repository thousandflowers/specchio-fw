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

static void testWake() {
  const int32_t lead = 30 * 60, insistAfter = 10 * 60;
  assert(wakePhase(-lead - 1, lead, insistAfter) == WAKE_WAITING);
  assert(wakePhase(-lead, lead, insistAfter) == WAKE_SUNRISE);
  assert(wakePhase(-1, lead, insistAfter) == WAKE_SUNRISE);
  assert(wakePhase(0, lead, insistAfter) == WAKE_HOLD);             // alarm time: full, steady
  assert(wakePhase(insistAfter - 1, lead, insistAfter) == WAKE_HOLD);
  assert(wakePhase(insistAfter, lead, insistAfter) == WAKE_INSIST);
  assert(wakePhase(insistAfter + WAKE_INSIST_S - 1, lead, insistAfter) == WAKE_INSIST);
  assert(wakePhase(insistAfter + WAKE_INSIST_S, lead, insistAfter) == WAKE_OVER);  // never flashes forever
  assert(wakePhase(600, lead, 0) == WAKE_HOLD && wakePhase(WAKE_INSIST_S, lead, 0) == WAKE_OVER);  // 0 = no insisting

  const float total = WAKE_INSIST_S;
  assert(near(wakeInsistFrame(0, total).v, 100));                    // starts from the full light
  float lowestEarly = 100, lowestLate = 100;
  for (float t = 0; t < 60; t += 0.02f) lowestEarly = fminf(lowestEarly, wakeInsistFrame(t, total).v);
  for (float t = total - 60; t < total; t += 0.02f) lowestLate = fminf(lowestLate, wakeInsistFrame(t, total).v);
  assert(lowestEarly > 60 && lowestLate < 5);                        // shallow at first, to black at the end
  // Count pulses in the first and in the last ten seconds: it speeds up, but
  // never beyond 2 per second.
  auto pulsesIn = [&](float from) {
    int count = 0;
    bool wasHigh = true;
    for (float t = from; t < from + 10; t += 0.01f) {
      bool isHigh = wakeInsistFrame(t, total).v > 99.0f - 30.0f * (WAKE_PULSE_DEPTH_START) / 2;
      if (isHigh && !wasHigh) count++;
      wasHigh = isHigh;
    }
    return count;
  };
  int early = pulsesIn(5), late = pulsesIn(total - 12);
  assert(early >= 1 && early <= 4 && late >= 15 && late <= 21);
  for (float t = 0; t <= total; t += 0.37f) {
    Hsv c = wakeInsistFrame(t, total);
    assert(c.v >= 0 && c.v <= 100 && c.s >= 0 && c.s <= 50.01f);
  }
  assert(near(wakeInsistFrame(total * 9, total).s, 0));              // past the end: clamped

  assert(secondsUntilClock(23, 0, 0, 7, 30) == 8 * 3600 + 1800);     // tonight for tomorrow morning
  assert(secondsUntilClock(7, 0, 0, 7, 30) == 1800);                 // later today
  assert(secondsUntilClock(7, 30, 0, 7, 30) == 86400);               // exactly now: tomorrow
  assert(secondsUntilClock(7, 30, 1, 7, 30) == 86399);
  assert(secondsUntilClock(0, 0, 0, 23, 59) == 86340);

  int h = -1, m = -1;
  assert(parseClock("07:30", h, m) && h == 7 && m == 30);
  assert(parseClock("23:59", h, m) && parseClock("00:00", h, m) && h == 0 && m == 0);
  h = 5; m = 6;
  assert(!parseClock("24:00", h, m) && !parseClock("7:30", h, m) && !parseClock("07-30", h, m));
  assert(!parseClock("07:60", h, m) && !parseClock("ab:cd", h, m) && !parseClock("", h, m) && !parseClock("07:300", h, m));
  assert(h == 5 && m == 6);                                          // bad input leaves outputs untouched
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
  testWake();
  testShimmer();
  testPulse();
  testSmoothing();
  testClock();
  puts("effects tests ok");
}
