// specchio: lean firmware for the Athom LS5050C RGB strip (ESP8266).
// HomeKit + local HTTP API + web OTA. Wi-Fi tuned for a weak signal:
// no modem sleep, max TX power, 802.11g by default, fast reconnect.
#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ESP8266HTTPUpdateServer.h>
#include <LittleFS.h>
#include <arduino_homekit_server.h>
#include "color.h"

#ifndef OTA_USER
#define OTA_USER "admin"
#endif
#ifndef OTA_PASS
#define OTA_PASS "specchio"
#endif

static const char *HOSTNAME = "specchio";
static const char *SETUP_AP = "specchio-setup";
static const char *SETUP_AP_PASS = "specchio1";
static const int BUTTON_PIN = 0;
static const uint32_t FADE_MS = 250;
static const uint32_t SAVE_DELAY_MS = 3000;     // debounce flash writes
static const uint32_t AP_AFTER_MS = 90000;      // offline this long -> setup AP
static const char *FW_VERSION = "1.0.0";

extern "C" homekit_server_config_t hk_config;
extern "C" homekit_characteristic_t cha_on, cha_bri, cha_hue, cha_sat;

ESP8266WebServer server(80);
ESP8266HTTPUpdateServer updater;

struct Light { bool on = true; float h = 30, s = 60, v = 100; } light;
int pins[3] = {12, 4, 14};  // R,G,B; change via /api/config?pins=
char phy = 'g';             // b|g|n; /api/config?phy=

float cur[3] = {0, 0, 0}, from[3] = {0, 0, 0}, target[3] = {0, 0, 0};
uint32_t fadeStart = 0, dirtyAt = 0, offlineSince = 0;
bool dirty = false, homekitStarted = false, apUp = false, safeMode = false;

// ---------- boot-loop guard ----------
// No serial adapter here: a crash loop would brick the strip. Count quick
// reboots in RTC memory; after 3 in a row skip HomeKit, keep web + /update.
static const uint32_t STABLE_MS = 30000;
static const uint32_t MAX_QUICK_BOOTS = 3;
struct BootGuard { uint32_t magic, count; };
static const uint32_t BOOT_MAGIC = 0x5bec0001;
bool bootStable = false;

void bootGuardStart() {
  BootGuard g;
  ESP.rtcUserMemoryRead(0, (uint32_t *)&g, sizeof g);
  if (g.magic != BOOT_MAGIC) g = {BOOT_MAGIC, 0};
  g.count++;
  safeMode = g.count > MAX_QUICK_BOOTS;
  ESP.rtcUserMemoryWrite(0, (uint32_t *)&g, sizeof g);
}

void bootGuardWatch() {
  if (bootStable || millis() < STABLE_MS) return;
  BootGuard g = {BOOT_MAGIC, 0};
  ESP.rtcUserMemoryWrite(0, (uint32_t *)&g, sizeof g);
  bootStable = true;
}

// ---------- persistence ----------
void loadConfig() {
  File f = LittleFS.open("/cfg", "r");
  if (!f) return;
  while (f.available()) {
    String line = f.readStringUntil('\n');
    line.trim();
    if (line.startsWith("pins=")) parsePins(line.c_str() + 5, pins);
    else if (line.startsWith("phy=") && line.length() == 5) phy = line[4];
  }
}

bool saveConfig() {
  File f = LittleFS.open("/cfg", "w");
  if (!f) return false;
  f.printf("pins=%d,%d,%d\nphy=%c\n", pins[0], pins[1], pins[2], phy);
  return true;
}

void loadState() {
  File f = LittleFS.open("/state", "r");
  if (!f) return;
  int on = 1;
  float h, s, v;
  if (sscanf(f.readString().c_str(), "%d %f %f %f", &on, &h, &s, &v) == 4) {
    light.on = on != 0; light.h = h; light.s = s; light.v = v;
  }
}

void saveState() {
  File f = LittleFS.open("/state", "w");
  if (f) f.printf("%d %.1f %.1f %.0f\n", light.on, light.h, light.s, light.v);
}

// ---------- output ----------
void writePwm() {
  for (int i = 0; i < 3; i++) analogWrite(pins[i], toDuty(cur[i]));
}

void applyLight() {
  Rgb c = light.on ? hsvToRgb(light.h, light.s, light.v) : Rgb{0, 0, 0};
  float t[3] = {c.r, c.g, c.b};
  for (int i = 0; i < 3; i++) { from[i] = cur[i]; target[i] = t[i]; }
  fadeStart = millis();
  dirty = true;
  dirtyAt = millis();
}

void fadeStep() {
  float k = (millis() - fadeStart) / (float)FADE_MS;
  if (k >= 1) k = 1;
  bool changed = false;
  for (int i = 0; i < 3; i++) {
    float n = from[i] + (target[i] - from[i]) * k;
    if (n != cur[i]) { cur[i] = n; changed = true; }
  }
  if (changed) writePwm();
}

void notifyHomekit() {
  cha_on.value.bool_value = light.on;
  cha_bri.value.int_value = (int)light.v;
  cha_hue.value.float_value = light.h;
  cha_sat.value.float_value = light.s;
  if (!homekitStarted) return;
  homekit_characteristic_notify(&cha_on, cha_on.value);
  homekit_characteristic_notify(&cha_bri, cha_bri.value);
  homekit_characteristic_notify(&cha_hue, cha_hue.value);
  homekit_characteristic_notify(&cha_sat, cha_sat.value);
}

// ---------- HomeKit setters ----------
void setOn(const homekit_value_t v) { cha_on.value = v; light.on = v.bool_value; applyLight(); }
void setBri(const homekit_value_t v) { cha_bri.value = v; light.v = v.int_value; applyLight(); }
void setHue(const homekit_value_t v) { cha_hue.value = v; light.h = v.float_value; applyLight(); }
void setSat(const homekit_value_t v) { cha_sat.value = v; light.s = v.float_value; applyLight(); }

// ---------- Wi-Fi ----------
WiFiPhyMode_t phyMode() {
  return phy == 'b' ? WIFI_PHY_MODE_11B : phy == 'n' ? WIFI_PHY_MODE_11N : WIFI_PHY_MODE_11G;
}

void startWifi() {
  WiFi.persistent(true);  // credentials live in the SDK area, which an OTA keeps
  WiFi.mode(WIFI_STA);
  WiFi.hostname(HOSTNAME);
  WiFi.setSleepMode(WIFI_NONE_SLEEP);  // stock fw modem-sleeps: the main source of lag
  WiFi.setPhyMode(phyMode());          // 11g/11b keep a higher TX power than 11n
  WiFi.setOutputPower(20.5);
  WiFi.setAutoReconnect(true);
  if (WiFi.SSID().length()) WiFi.begin();
  offlineSince = millis();
}

void wifiWatch() {
  if (WiFi.status() == WL_CONNECTED) {
    offlineSince = millis();
    if (!homekitStarted && !safeMode) { arduino_homekit_setup(&hk_config); homekitStarted = true; }
    if (apUp && WiFi.softAPgetStationNum() == 0) { WiFi.softAPdisconnect(true); apUp = false; }
    return;
  }
  if (!apUp && millis() - offlineSince > AP_AFTER_MS) {
    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(SETUP_AP, SETUP_AP_PASS);
    apUp = true;
  }
}

// ---------- web ----------
static const char PAGE[] PROGMEM = R"html(<!doctype html><meta name=viewport content="width=device-width">
<title>specchio</title><style>body{font:16px -apple-system,sans-serif;max-width:420px;margin:24px auto;padding:0 16px}
input,button{font:inherit;margin:4px 0}pre{background:#eee;padding:8px;white-space:pre-wrap}</style>
<h2>specchio</h2>
<button onclick="go('on=1')">On</button> <button onclick="go('on=0')">Off</button><br>
<input type=color id=c onchange="go('hex='+c.value.slice(1))"><br>
<input type=range min=1 max=100 id=v onchange="go('v='+v.value)" style="width:100%">
<pre id=s></pre>
<details><summary>Wi-Fi</summary><form method=post action=/api/wifi>
<input name=ssid placeholder=SSID><br><input name=pass type=password placeholder=password><br><button>Save &amp; reboot</button></form></details>
<p><a href=/update>Firmware update</a></p>
<script>
async function go(q){await fetch('/api/set?'+q);load()}
async function load(){const j=await (await fetch('/api/state')).json();s.textContent=JSON.stringify(j,null,1);v.value=j.v}
load();setInterval(load,5000)
</script>)html";

void sendState() {
  char buf[400];
  snprintf(buf, sizeof buf,
    "{\"on\":%s,\"h\":%.1f,\"s\":%.1f,\"v\":%.0f,\"rssi\":%d,\"ssid\":\"%s\",\"phy\":\"%c\","
    "\"pins\":\"%d,%d,%d\",\"heap\":%u,\"uptime_s\":%lu,\"homekit_clients\":%d,\"safe_mode\":%s,\"fw\":\"%s\"}",
    light.on ? "true" : "false", light.h, light.s, light.v, WiFi.RSSI(), WiFi.SSID().c_str(), phy,
    pins[0], pins[1], pins[2], ESP.getFreeHeap(), millis() / 1000,
    homekitStarted ? arduino_homekit_connected_clients_count() : 0, safeMode ? "true" : "false", FW_VERSION);
  server.send(200, "application/json", buf);
}

void handleSet() {
  if (server.hasArg("on")) light.on = server.arg("on") != "0";
  if (server.hasArg("hex")) {
    float h, s, v;
    if (!hexToHsv(server.arg("hex").c_str(), h, s, v)) return server.send(400, "text/plain", "bad hex");
    light.h = h; light.s = s; light.on = true;
  }
  if (server.hasArg("h")) light.h = constrain(server.arg("h").toFloat(), 0, 360);
  if (server.hasArg("s")) light.s = constrain(server.arg("s").toFloat(), 0, 100);
  if (server.hasArg("v")) light.v = constrain(server.arg("v").toFloat(), 1, 100);
  applyLight();
  notifyHomekit();
  sendState();
}

void handleConfig() {
  bool changed = false;
  if (server.hasArg("pins")) {
    int p[3];
    if (!parsePins(server.arg("pins").c_str(), p)) return server.send(400, "text/plain", "pins: 3 of 4,5,12,13,14,15");
    for (int i = 0; i < 3; i++) { analogWrite(pins[i], 0); pins[i] = p[i]; pinMode(pins[i], OUTPUT); }
    writePwm();
    changed = true;
  }
  if (server.hasArg("phy")) {
    String m = server.arg("phy");
    if (m != "b" && m != "g" && m != "n") return server.send(400, "text/plain", "phy: b|g|n");
    phy = m[0];
    WiFi.setPhyMode(phyMode());  // takes effect on next (re)connect
    changed = true;
  }
  if (changed && !saveConfig()) return server.send(500, "text/plain", "flash write failed");
  sendState();
}

void handleWifi() {
  String ssid = server.arg("ssid");
  if (ssid.isEmpty() || ssid.length() > 32) return server.send(400, "text/plain", "ssid required");
  server.send(200, "text/plain", "saved, rebooting");
  delay(200);
  WiFi.begin(ssid.c_str(), server.arg("pass").c_str());  // persistent=true stores it
  delay(500);
  ESP.restart();
}

void handleHomekitReset() {
  server.send(200, "text/plain", "HomeKit pairing erased, rebooting");
  homekit_storage_reset();
  delay(200);
  ESP.restart();
}

void startWeb() {
  server.on("/", [] { server.send_P(200, "text/html", PAGE); });
  server.on("/api/state", sendState);
  server.on("/api/set", handleSet);
  server.on("/api/config", handleConfig);
  server.on("/api/wifi", HTTP_POST, handleWifi);
  server.on("/api/homekit-reset", HTTP_POST, handleHomekitReset);
  server.on("/api/reboot", HTTP_POST, [] { server.send(200, "text/plain", "rebooting"); delay(200); ESP.restart(); });
  updater.setup(&server, "/update", OTA_USER, OTA_PASS);  // safety net: always reflashable
  server.begin();
}

// ---------- button ----------
void buttonWatch() {
  static bool last = HIGH;
  static uint32_t changedAt = 0;
  bool now = digitalRead(BUTTON_PIN);
  if (now == last || millis() - changedAt < 40) return;
  changedAt = millis();
  last = now;
  if (now == HIGH) { light.on = !light.on; applyLight(); notifyHomekit(); }  // on release
}

void setup() {
  bootGuardStart();
  analogWriteRange(PWM_MAX);
  analogWriteFreq(1000);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  if (!LittleFS.begin()) { LittleFS.format(); LittleFS.begin(); }
  loadConfig();
  loadState();
  for (int i = 0; i < 3; i++) pinMode(pins[i], OUTPUT);
  applyLight();  // restore last colour immediately, before Wi-Fi
  notifyHomekit();
  cha_on.setter = setOn;
  cha_bri.setter = setBri;
  cha_hue.setter = setHue;
  cha_sat.setter = setSat;
  startWifi();
  startWeb();
}

void loop() {
  bootGuardWatch();
  wifiWatch();
  if (homekitStarted) arduino_homekit_loop();
  server.handleClient();
  buttonWatch();
  fadeStep();
  if (dirty && millis() - dirtyAt > SAVE_DELAY_MS) { saveState(); dirty = false; }
}
