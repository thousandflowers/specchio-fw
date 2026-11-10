// Pure decisions and small utilities: no Arduino headers, tested on the Mac
// (test/test_policy.cpp). Anything that can brick the strip is decided here.
#pragma once
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// ---------- roaming ----------
// The ESP8266 never changes access point by itself. When the signal is weak
// we scan and move only if another AP of the same network is clearly better.
static const int ROAM_BELOW_DBM = -72;
static const int ROAM_MARGIN_DB = 8;

inline bool shouldRoam(int currentRssi, int candidateRssi, bool isSameAp) {
  if (isSameAp) return false;
  if (currentRssi >= 0 || candidateRssi >= 0) return false;  // SDK reports 31 when unknown
  if (currentRssi > ROAM_BELOW_DBM) return false;
  return candidateRssi - currentRssi >= ROAM_MARGIN_DB;
}

// ---------- button ----------
static const uint32_t PRESS_DEBOUNCE_MS = 40;
static const uint32_t PRESS_SETUP_MS = 5000;
static const uint32_t PRESS_RESET_MS = 15000;

enum Press { PRESS_NONE, PRESS_TOGGLE, PRESS_SETUP_AP, PRESS_FACTORY_RESET };

inline Press classifyPress(uint32_t heldMs) {
  if (heldMs < PRESS_DEBOUNCE_MS) return PRESS_NONE;
  if (heldMs < PRESS_SETUP_MS) return PRESS_TOGGLE;
  if (heldMs < PRESS_RESET_MS) return PRESS_SETUP_AP;
  return PRESS_FACTORY_RESET;
}

// ---------- boot-loop guard ----------
// Kept in RTC memory: survives crashes, is garbage after a power cut.
static const uint32_t BOOT_MAGIC = 0x5bec0002;
static const uint32_t CRASHES_FOR_SAFE_MODE = 3;

struct BootGuard { uint32_t magic, count, isCleanRestart; };

// ESP8266 rst_reason: 0 power-on, 1 hardware watchdog, 2 exception,
// 3 software watchdog, 4 software restart, 5 deep-sleep wake, 6 reset pin.
inline bool isCrashReset(uint32_t reason) { return reason >= 1 && reason <= 3; }  // has crash details

// Power-on and the reset pin mean a person intervened. Everything else is a
// reboot nobody asked for unless we flagged it ourselves: abort(), running
// out of memory and the HomeKit library's own restart all report "4".
inline bool isUnattendedReset(uint32_t reason) { return reason != 0 && reason != 5 && reason != 6; }

inline void bootGuardMarkStable(BootGuard &g) { g = {BOOT_MAGIC, 0, 0}; }
inline void bootGuardMarkClean(BootGuard &g) { g = {BOOT_MAGIC, 0, 1}; }

// Call once per boot. Returns true when this boot must run in safe mode.
inline bool bootGuardEnter(BootGuard &g, uint32_t resetReason) {
  if (g.magic != BOOT_MAGIC) bootGuardMarkStable(g);
  bool wasClean = g.isCleanRestart != 0;
  g.isCleanRestart = 0;
  if (!isUnattendedReset(resetReason)) g.count = 0;
  else if (!wasClean) g.count++;
  return g.count >= CRASHES_FOR_SAFE_MODE;
}

// ---------- settings ----------
inline bool isPhy(char c) { return c && strchr("bgn", c); }
inline bool isBootMode(char c) { return c && strchr("l10", c); }  // last, on, off

// Reads "key=value" from newline-separated text. False if missing or too long.
inline bool kvGet(const char *text, const char *key, char *out, size_t outSize) {
  size_t keyLen = strlen(key);
  for (const char *p = text; p && *p;) {
    const char *eol = strchr(p, '\n');
    size_t len = eol ? (size_t)(eol - p) : strlen(p);
    if (len > keyLen && strncmp(p, key, keyLen) == 0 && p[keyLen] == '=') {
      size_t valueLen = len - keyLen - 1;
      if (valueLen && p[len - 1] == '\r') valueLen--;
      if (valueLen >= outSize) return false;
      memcpy(out, p + keyLen + 1, valueLen);
      out[valueLen] = '\0';
      return true;
    }
    p = eol ? eol + 1 : nullptr;
  }
  return false;
}

// ---------- Wi-Fi disconnect reasons worth naming ----------
inline const char *discReasonName(int reason) {
  static const struct { int code; const char *name; } names[] = {
    {2, "auth expired"}, {4, "kicked for inactivity"}, {8, "left by request"},
    {15, "handshake timeout"}, {200, "beacon lost (weak signal)"},
    {201, "network not found"}, {202, "wrong password"}, {204, "handshake timeout"},
  };
  for (const auto &n : names) if (n.code == reason) return n.name;
  return "other";
}

// ---------- event log ----------
// Fixed-size ring in RAM, read over HTTP: the only debug channel on a device
// with no serial port wired.
class EventLog {
 public:
  static const int LINES = 24;
  static const int LEN = 120;

  void add(uint32_t uptimeSec, const char *fmt, ...) __attribute__((format(printf, 3, 4))) {
    char *line = buf_[(head_ + count_) % LINES];
    if (count_ < LINES) count_++; else head_ = (head_ + 1) % LINES;
    int n = snprintf(line, LEN, "[%6lus] ", (unsigned long)uptimeSec);
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line + n, LEN - n, fmt, ap);
    va_end(ap);
  }
  int size() const { return count_; }
  const char *line(int i) const { return buf_[(head_ + i) % LINES]; }  // 0 = oldest

 private:
  char buf_[LINES][LEN];
  int head_ = 0, count_ = 0;
};
