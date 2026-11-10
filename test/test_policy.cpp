// Run: c++ -std=c++11 -Wall -Isrc test/test_policy.cpp -o /tmp/t && /tmp/t
#include <cassert>
#include <cstdio>
#include "policy.h"

static void testRoam() {
  assert(shouldRoam(-88, -60, false));    // weak now, much better AP nearby
  assert(!shouldRoam(-88, -60, true));    // it is the AP we are already on
  assert(!shouldRoam(-88, -82, false));   // 6 dB better: not worth a drop
  assert(shouldRoam(-88, -80, false));    // exactly the margin
  assert(!shouldRoam(-60, -40, false));   // signal already fine, do not touch
  assert(!shouldRoam(31, -60, false));    // 31 = SDK "not connected"
  assert(!shouldRoam(-88, 31, false));
}

static void testPress() {
  assert(classifyPress(10) == PRESS_NONE);          // contact bounce
  assert(classifyPress(200) == PRESS_TOGGLE);
  assert(classifyPress(4999) == PRESS_TOGGLE);
  assert(classifyPress(5000) == PRESS_SETUP_AP);
  assert(classifyPress(14999) == PRESS_SETUP_AP);
  assert(classifyPress(15000) == PRESS_FACTORY_RESET);
}

static void testBootGuard() {
  const uint32_t POWER_ON = 0, HW_WDT = 1, EXCEPTION = 2, SOFT_RESTART = 4, RESET_PIN = 6;

  BootGuard g = {0xdeadbeef, 77, 9};                // RTC garbage after power-on
  assert(!bootGuardEnter(g, POWER_ON) && g.count == 0 && g.magic == BOOT_MAGIC);

  assert(!bootGuardEnter(g, EXCEPTION) && g.count == 1);
  // abort(), out-of-memory and the HomeKit library's own restart all show up
  // as a plain software restart: they must count too.
  assert(!bootGuardEnter(g, SOFT_RESTART) && g.count == 2);
  assert(bootGuardEnter(g, HW_WDT));                // third in a row -> safe mode
  assert(bootGuardEnter(g, SOFT_RESTART));          // still looping -> stays safe

  bootGuardMarkStable(g);                           // ran long enough: forgiven
  assert(g.count == 0);
  assert(!bootGuardEnter(g, SOFT_RESTART) && g.count == 1);

  bootGuardMarkClean(g);                            // reboot we asked for
  assert(!bootGuardEnter(g, SOFT_RESTART) && g.count == 0);
  assert(!bootGuardEnter(g, SOFT_RESTART) && g.count == 1);  // the flag is one-shot

  g.count = 2;
  assert(!bootGuardEnter(g, RESET_PIN) && g.count == 0);     // a human power-cycled it

  BootGuard garbage = {0x12345678, 2, 1};           // garbage that looks like "clean"
  assert(!bootGuardEnter(garbage, EXCEPTION) && garbage.count == 1);

  assert(!isCrashReset(0) && isCrashReset(1) && isCrashReset(2) && isCrashReset(3) && !isCrashReset(4));
}

static void testKv() {
  const char *text = "pins=12,4,14\nphy=g\r\nfade=250\nlast=x";
  char v[16];
  assert(kvGet(text, "pins", v, sizeof v) && !strcmp(v, "12,4,14"));
  assert(kvGet(text, "phy", v, sizeof v) && !strcmp(v, "g"));   // CR stripped
  assert(kvGet(text, "fade", v, sizeof v) && !strcmp(v, "250"));
  assert(kvGet(text, "last", v, sizeof v) && !strcmp(v, "x"));  // no trailing newline
  assert(!kvGet(text, "pin", v, sizeof v));                     // prefix of a key
  assert(!kvGet(text, "missing", v, sizeof v));
  assert(!kvGet("", "pins", v, sizeof v));
  char tiny[4];
  assert(!kvGet(text, "pins", tiny, sizeof tiny));              // would overflow
  assert(kvGet("k=\n", "k", v, sizeof v) && v[0] == '\0');      // empty value

  assert(isPhy('b') && isPhy('g') && isPhy('n') && !isPhy('x') && !isPhy('\0'));
  assert(isBootMode('l') && isBootMode('1') && isBootMode('0') && !isBootMode('\0'));
}

static void testLog() {
  EventLog log;
  assert(log.size() == 0);
  log.add(5, "wifi up %d", -88);
  assert(log.size() == 1 && strstr(log.line(0), "wifi up -88") && strstr(log.line(0), "5s]"));

  for (int i = 0; i < EventLog::LINES + 3; i++) log.add(i, "n%d", i);
  assert(log.size() == EventLog::LINES);                        // ring is full
  assert(strstr(log.line(0), "n3"));                            // oldest kept
  assert(strstr(log.line(EventLog::LINES - 1), "n26"));         // newest

  char big[400];
  memset(big, 'x', sizeof big - 1);
  big[sizeof big - 1] = '\0';
  log.add(1, "%s", big);                                        // must truncate, not overflow
  assert(strlen(log.line(EventLog::LINES - 1)) == EventLog::LEN - 1);

  assert(!strcmp(discReasonName(200), "beacon lost (weak signal)"));
  assert(!strcmp(discReasonName(9999), "other"));
}

int main() {
  testRoam();
  testPress();
  testBootGuard();
  testKv();
  testLog();
  puts("policy tests ok");
}
