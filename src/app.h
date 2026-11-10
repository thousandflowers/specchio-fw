// Shared state and entry points between main.cpp, net.cpp and web.cpp.
#pragma once
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <TZ.h>
#include "color.h"
#include "effects.h"
#include "policy.h"

#define FW_VERSION "2.2.1"
#define HOSTNAME "specchio"
#define SETUP_AP_SSID "specchio-setup"
#define TIMEZONE TZ_Europe_Rome  // for the hourly tick

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
void setRaw(int r, int g, int b);     // direct PWM duty, for wiring tests
bool startEffect(const char *key, float minutes);  // minutes <= 0: the effect's default
void stopEffect();
const char *effectName();             // "" when none is running
uint32_t effectSecondsLeft();
void flash(float hue, float saturation, int count);  // coloured blinks over whatever is showing
void setShimmer(int amount);
void setHourly(bool isOn);
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
void webBegin();
void webTick();
