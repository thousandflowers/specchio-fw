// Shared state and entry points between main.cpp, net.cpp and web.cpp.
#pragma once
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include "color.h"
#include "policy.h"

#define FW_VERSION "2.0.0"
#define HOSTNAME "specchio"
#define SETUP_AP_SSID "specchio-setup"

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
void restartClean();                  // intentional reboot: does not count as a crash
void factoryReset();
bool homekitPaired();
int homekitClients();

// net.cpp
extern uint32_t netDisconnects;
extern int netLastReason;
void netBegin();
void netTick();
void netStartSetupAp();
void netRequestRoamScan();

// web.cpp
void webBegin();
void webTick();
