// Shared state and entry points between main.cpp, net.cpp and web.cpp.
#pragma once
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <TZ.h>
#include "color.h"
#include "effects.h"
#include "policy.h"

#define FW_VERSION "2.7.1"
#define HOSTNAME "specchio"
#define SETUP_AP_SSID "specchio-setup"
#define TIMEZONE TZ_Europe_Rome  // for the hourly tick
#ifndef NTP_FALLBACK
#define NTP_FALLBACK "time.google.com"  // second time server; secrets.ini can name one on the home network
#endif

// Per-device secrets come from secrets.ini (gitignored); these are fallbacks
// so the project still builds without it.
#ifndef OTA_USER
#define OTA_USER "admin"
#endif
#ifndef OTA_PASS
#define OTA_PASS "specchio-change-me"
#endif
#ifndef WIFI_SSID
#define WIFI_SSID ""
#endif
#ifndef WIFI_PASS
#define WIFI_PASS ""
#endif

struct Config {
  int pins[3];        // R, G, B GPIOs
  char phy;           // 'b' | 'g' | 'n'
  char boot;          // power-on state: 'l' last | '1' on | '0' off
  uint16_t fadeMs;
  bool provisioned;   // has joined Wi-Fi at least once with this firmware
  uint8_t txDbm;      // max TX power, 0 = leave the SDK default
  uint8_t shimmer;    // 0-100: how much the idle lamp "vibrates", 0 = still
  bool isHourly;      // tick the lamp at every change of hour
  uint32_t wakeAt;        // epoch of the next alarm the phone told us about, 0 = none
  uint8_t wakeLeadMin;    // sunrise starts this long before it
  uint8_t wakeInsistMin;  // pulses start this long after it, 0 = never
  bool isClaudeOn;        // blink when Claude finishes answering (flashes sent with src=claude)
};

struct Light { bool on; float h, s, v; };  // HomeKit units: 0-360, 0-100, 0-100

extern Config cfg;
extern Light light;
extern EventLog elog;
extern bool safeMode;

#define LOG(...) elog.add(millis() / 1000, __VA_ARGS__)

// main.cpp
bool saveConfig();
void setPins(const int pins[3]);
void applyLight();                    // fade to `light`, tell HomeKit, schedule a save
bool setRaw(int r, int g, int b);     // direct PWM duty (wiring tests, Mac scenes); false while a wake-up runs
bool startEffect(const char *key, float minutes);  // minutes <= 0: the effect's default
void stopEffect();
const char *effectName();             // "" when none is running
uint32_t effectSecondsLeft();
void flash(float hue, float saturation, int count);  // coloured blinks over whatever is showing
void setShimmer(int amount);
void setHourly(bool isOn);
void setClaude(bool isOn);  // lets through or mutes the flashes tagged src=claude
bool setScene(const char *key, bool isOn);  // a Home switch for a scene the Mac plays; false = unknown key
void scenesOn(char *out, size_t size);      // comma-separated keys of the scenes switched on
// Wake-up light for the next alarm. False when the strip does not know the time yet.
bool setWake(int hour, int minute, int leadMinutes, int insistMinutes);
void cancelWake();           // forget the alarm, leave the lamp alone
void acknowledgeWake();      // "I am up": stop the sequence, lamp stays on
const char *wakePhaseName(); // "" when no alarm is set
void spinFor(uint32_t seconds);       // experiment: no rest in the main loop for a while
uint32_t loopsPerSecond();
String stallReport();                 // which part of the loop kept the others waiting, and for how long
void restartClean();                  // intentional reboot: does not count as a crash
void factoryReset();
bool homekitPaired();
int homekitClients();

// net.cpp
extern uint32_t netDisconnects;
extern uint32_t netFailedJoins;
extern int netLastReason;
void netBegin();
void netTick();
void netStartSetupAp();
void netRequestRoamScan();

// web.cpp
#ifdef SELF_RESTORE  // experiment builds: see guard.cpp
void guardBoot();
void guardTick();
void guardReached();  // a request from the home network was answered
int guardRestore();  // 0 = bootloader armed: ESP.restart() now
void guardDescribe(char *out, size_t len);
#endif
void webBegin();
void webTick();
