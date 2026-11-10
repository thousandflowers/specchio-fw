// specchio: firmware for the Athom LS5050C RGB strip controller (ESP8266).
// Light output, persistence, button, HomeKit glue and the boot-loop guard.
// Wi-Fi lives in net.cpp, the HTTP API in web.cpp, pure logic in policy.h.
#include <LittleFS.h>
#include <arduino_homekit_server.h>
#include "app.h"

extern "C" {
extern homekit_server_config_t hk_config;
extern homekit_characteristic_t cha_on, cha_bri, cha_hue, cha_sat;
int homekit_storage_reset();
}

static const int BUTTON_PIN = 0;
static const int SPARE_CHANNEL_PIN = 13;     // 4th MOSFET on this board, unused with RGB strips
static const int PWM_FREQ_HZ = 1000;
static const uint32_t SAVE_DELAY_MS = 3000;  // debounce flash writes
// A boot counts as good only after HomeKit has been running for a while:
// its start-up (key generation, ~10 s with watchdogs off) is the riskiest code.
static const uint32_t STABLE_MS = 120000;
static const uint32_t BLINK_MS = 120;
static const char *CONFIG_PATH = "/cfg";
static const char *STATE_PATH = "/state";

Config cfg = {{12, 4, 14}, 'g', 'l', 250, false};
Light light = {true, 30, 60, 100};  // warm white until a saved state is loaded
EventLog elog;
bool safeMode = false;

static float cur[3], fadeFrom[3], fadeTo[3];  // perceived level 0..1 per channel
static uint32_t fadeStart, dirtyAt;
static bool isFading, isRaw, isDirty, isHomekitUp, isBootStable;

// ---------- boot-loop guard ----------
static void bootGuardWrite(BootGuard g) { ESP.rtcUserMemoryWrite(0, (uint32_t *)&g, sizeof g); }

static void bootGuardStart() {
  BootGuard g;
  ESP.rtcUserMemoryRead(0, (uint32_t *)&g, sizeof g);
  safeMode = bootGuardEnter(g, ESP.getResetInfoPtr()->reason);
  bootGuardWrite(g);
}

static void bootGuardTick() {
  if (isBootStable || millis() < STABLE_MS || !(isHomekitUp || safeMode)) return;
  isBootStable = true;
  BootGuard g;
  bootGuardMarkStable(g);
  bootGuardWrite(g);
}

void restartClean() {
  BootGuard g;
  bootGuardMarkClean(g);
  bootGuardWrite(g);
  delay(300);  // let the HTTP reply leave
  ESP.restart();
}

// ---------- persistence ----------
static String readFile(const char *path) {
  File f = LittleFS.open(path, "r");
  return f ? f.readString() : String();
}

// LittleFS reports a failed write only when the file is closed, so the one
// reliable check is to read it back.
static bool writeFile(const char *path, const String &text) {
  File f = LittleFS.open(path, "w");
  if (!f) return false;
  f.print(text);
  f.close();
  return readFile(path) == text;
}

static void loadConfig() {
  String text = readFile(CONFIG_PATH);
  char v[24];
  if (kvGet(text.c_str(), "pins", v, sizeof v)) parsePins(v, cfg.pins);
  if (kvGet(text.c_str(), "phy", v, sizeof v) && isPhy(v[0]) && !v[1]) cfg.phy = v[0];
  if (kvGet(text.c_str(), "boot", v, sizeof v) && isBootMode(v[0]) && !v[1]) cfg.boot = v[0];
  if (kvGet(text.c_str(), "fade", v, sizeof v)) cfg.fadeMs = constrain(atoi(v), 0, 5000);
  if (kvGet(text.c_str(), "prov", v, sizeof v)) cfg.provisioned = v[0] == '1';
}

bool saveConfig() {
  char text[80];
  snprintf(text, sizeof text, "pins=%d,%d,%d\nphy=%c\nboot=%c\nfade=%u\nprov=%d\n", cfg.pins[0],
           cfg.pins[1], cfg.pins[2], cfg.phy, cfg.boot, cfg.fadeMs, cfg.provisioned);
  return writeFile(CONFIG_PATH, text);
}

static void loadState() {
  String text = readFile(STATE_PATH);
  char v[16];
  if (kvGet(text.c_str(), "on", v, sizeof v)) light.on = v[0] == '1';
  if (kvGet(text.c_str(), "h", v, sizeof v)) light.h = clampf(atof(v), 0, 360);
  if (kvGet(text.c_str(), "s", v, sizeof v)) light.s = clampf(atof(v), 0, 100);
  if (kvGet(text.c_str(), "v", v, sizeof v)) light.v = clampf(atof(v), 1, 100);
  if (cfg.boot != 'l') light.on = cfg.boot == '1';
}

static void saveState() {
  char text[64];
  snprintf(text, sizeof text, "on=%d\nh=%s\ns=%s\nv=%d\n", light.on, String(light.h, 1).c_str(),
           String(light.s, 1).c_str(), (int)light.v);
  if (!writeFile(STATE_PATH, text)) LOG("could not save light state");
}

// ---------- output ----------
static void writePwm() {
  for (int i = 0; i < 3; i++) analogWrite(cfg.pins[i], toDuty(cur[i]));
}

static void claimPins() {
  for (int pin : cfg.pins) pinMode(pin, OUTPUT);
  bool isSpareUsed = false;
  for (int pin : cfg.pins) isSpareUsed |= pin == SPARE_CHANNEL_PIN;
  if (!isSpareUsed) { pinMode(SPARE_CHANNEL_PIN, OUTPUT); digitalWrite(SPARE_CHANNEL_PIN, LOW); }
}

void setPins(const int pins[3]) {
  for (int pin : cfg.pins) analogWrite(pin, 0);
  for (int i = 0; i < 3; i++) cfg.pins[i] = pins[i];
  claimPins();
  writePwm();
}

static void notifyHomekit() {
  cha_on.value.bool_value = light.on;
  cha_bri.value.int_value = (int)light.v;
  cha_hue.value.float_value = light.h;
  cha_sat.value.float_value = light.s;
  if (!isHomekitUp) return;
  homekit_characteristic_notify(&cha_on, cha_on.value);
  homekit_characteristic_notify(&cha_bri, cha_bri.value);
  homekit_characteristic_notify(&cha_hue, cha_hue.value);
  homekit_characteristic_notify(&cha_sat, cha_sat.value);
}

static void startFade() {
  Rgb c = light.on ? hsvToRgb(light.h, light.s, light.v) : Rgb{0, 0, 0};
  float to[3] = {c.r, c.g, c.b};
  for (int i = 0; i < 3; i++) { fadeFrom[i] = cur[i]; fadeTo[i] = to[i]; }
  fadeStart = millis();
  isFading = true;
  isRaw = false;
  isDirty = true;
  dirtyAt = millis();
}

void applyLight() {
  startFade();
  notifyHomekit();
}

void setRaw(int r, int g, int b) {
  int duty[3] = {r, g, b};
  isRaw = true;
  isFading = false;
  for (int i = 0; i < 3; i++) analogWrite(cfg.pins[i], constrain(duty[i], 0, PWM_MAX));
}

static void fadeTick() {
  if (!isFading) return;
  uint32_t elapsed = millis() - fadeStart;
  float k = cfg.fadeMs == 0 || elapsed >= cfg.fadeMs ? 1.0f : elapsed / (float)cfg.fadeMs;
  for (int i = 0; i < 3; i++) cur[i] = fadeFrom[i] + (fadeTo[i] - fadeFrom[i]) * k;
  writePwm();
  if (k >= 1.0f) isFading = false;
}

static void blink(int times) {
  for (int i = 0; i < times; i++) {
    setRaw(0, 0, 0);
    delay(BLINK_MS);
    setRaw(PWM_MAX / 4, PWM_MAX / 4, PWM_MAX / 4);
    delay(BLINK_MS);
  }
  startFade();
}

// ---------- HomeKit ----------
// Setters come from the Home app: update, fade, no echo back to HomeKit.
static void hkSetOn(const homekit_value_t v) { cha_on.value = v; light.on = v.bool_value; startFade(); }
static void hkSetBri(const homekit_value_t v) { cha_bri.value = v; light.v = clampf(v.int_value, 0, 100); startFade(); }
static void hkSetHue(const homekit_value_t v) { cha_hue.value = v; light.h = clampf(v.float_value, 0, 360); startFade(); }
static void hkSetSat(const homekit_value_t v) { cha_sat.value = v; light.s = clampf(v.float_value, 0, 100); startFade(); }

static void hkEvent(homekit_event_t event) {
  if (event == HOMEKIT_EVENT_PAIRING_ADDED) LOG("homekit: paired");
  if (event == HOMEKIT_EVENT_PAIRING_REMOVED) LOG("homekit: pairing removed");
}

static void homekitStart() {
  cha_on.setter = hkSetOn;
  cha_bri.setter = hkSetBri;
  cha_hue.setter = hkSetHue;
  cha_sat.setter = hkSetSat;
  hk_config.on_event = hkEvent;
  arduino_homekit_setup(&hk_config);
  isHomekitUp = true;
  LOG("homekit up, %s", homekitPaired() ? "paired" : "waiting for pairing");
}

bool homekitPaired() {
  homekit_server_t *server = arduino_homekit_get_running_server();
  return server && server->paired;
}

int homekitClients() { return isHomekitUp ? arduino_homekit_connected_clients_count() : 0; }

void factoryReset() {
  LOG("factory reset");
  homekit_storage_reset();
  LittleFS.remove(CONFIG_PATH);
  LittleFS.remove(STATE_PATH);
  WiFi.persistent(true);
  WiFi.disconnect();  // with persistent on, this erases the saved credentials
  restartClean();
}

// ---------- button ----------
static void buttonTick() {
  static bool wasDown = false;
  static uint32_t downAt = 0;
  static Press fired = PRESS_NONE;
  bool isDown = digitalRead(BUTTON_PIN) == LOW;
  uint32_t now = millis();

  if (isDown && !wasDown) { downAt = now; fired = PRESS_NONE; }
  Press press = isDown || wasDown ? classifyPress(now - downAt) : PRESS_NONE;

  if (isDown && press == PRESS_SETUP_AP && fired != PRESS_SETUP_AP) {
    fired = press;
    blink(1);  // feedback: release now for the setup AP, keep holding to reset
    netStartSetupAp();
  } else if (isDown && press == PRESS_FACTORY_RESET) {
    blink(3);
    factoryReset();
  } else if (!isDown && wasDown && fired == PRESS_NONE && press == PRESS_TOGGLE) {
    light.on = !light.on;
    applyLight();
  }
  wasDown = isDown;
}

void setup() {
  bootGuardStart();
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  analogWriteRange(PWM_MAX);
  analogWriteFreq(PWM_FREQ_HZ);
  LOG("boot fw %s: %s", FW_VERSION, ESP.getResetReason().c_str());
  if (isCrashReset(ESP.getResetInfoPtr()->reason)) LOG("%s", ESP.getResetInfo().c_str());

  if (safeMode) {
    // Three crashes in a row: skip flash storage and HomeKit, keep the lamp,
    // the web page and /update so a fixed firmware can be uploaded.
    LOG("SAFE MODE: storage and HomeKit skipped, upload a firmware at /update");
  } else {
    if (!LittleFS.begin()) LOG("storage mount failed, using defaults");
    loadConfig();
    loadState();
  }
  claimPins();
  startFade();  // light comes back before Wi-Fi does
  isDirty = false;
  notifyHomekit();
  netBegin();
  webBegin();
}

void loop() {
  bootGuardTick();
  netTick();
  if (!safeMode && !isHomekitUp && WiFi.status() == WL_CONNECTED) homekitStart();
  if (isHomekitUp) arduino_homekit_loop();
  webTick();
  buttonTick();
  fadeTick();
  if (isDirty && !safeMode && millis() - dirtyAt > SAVE_DELAY_MS) {
    isDirty = false;
    saveState();
  }
}
