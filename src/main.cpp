// specchio: firmware for the Athom LS5050C RGB strip controller (ESP8266).
// Light output and effects, persistence, button, HomeKit glue, boot-loop guard.
// Wi-Fi lives in net.cpp, the HTTP API in web.cpp, pure logic in policy.h,
// effect maths in effects.h.
#include <LittleFS.h>
#include <arduino_homekit_server.h>
#include <time.h>
#include "app.h"
#include "effects.h"

extern "C" {
extern homekit_server_config_t hk_config;
extern homekit_characteristic_t cha_on, cha_bri, cha_hue, cha_sat;
extern homekit_characteristic_t cha_fx_alba, cha_fx_focus, cha_shimmer, cha_hourly;
int homekit_storage_reset();
}

static const int BUTTON_PIN = 0;
static const int SPARE_CHANNEL_PIN = 13;     // 4th MOSFET on this board, unused with RGB strips
static const int PWM_FREQ_HZ = 1000;
static const uint32_t RENDER_MS = 20;        // 50 frames per second
static const uint32_t SAVE_DELAY_MS = 3000;  // debounce flash writes
// A boot counts as good only after HomeKit has been running for a while:
// its start-up (key generation, ~10 s with watchdogs off) is the riskiest code.
static const uint32_t STABLE_MS = 120000;
static const uint32_t STABLE_OFFLINE_MS = 300000;  // HomeKit cannot start without Wi-Fi: do not wait for it forever
static const uint32_t BLINK_MS = 120;
static const time_t MIN_VALID_EPOCH = 1700000000;  // before this the clock is not set yet
static const int DEFAULT_SHIMMER = 30;
static const Hsv EFFECT_DONE_COLOUR = {0, 100, 100};
static const char *CONFIG_PATH = "/cfg";
static const char *STATE_PATH = "/state";

// Radio defaults match the stock firmware (802.11b/g/n, SDK TX power): on the
// first flash a forced 11g mode lost the link within a minute.
Config cfg = {{12, 4, 14}, 'n', 'l', 250, false, 0, 0, false};
Light light = {true, 30, 60, 100};  // warm white until a saved state is loaded
EventLog elog;
bool safeMode = false;

// ---------- timed effects ----------
enum FxEnd { FX_END_KEEP_LAST_FRAME, FX_END_RESTORE_AND_FLASH };

struct FxDef {
  const char *key;                          // name in the HTTP API
  homekit_characteristic_t *homekitSwitch;  // its switch in the Home app
  float defaultMinutes;
  Hsv (*frame)(float elapsedSec, float totalSec, const Hsv &lightBefore);
  FxEnd end;
};

static const FxDef EFFECTS[] = {
  {"alba", &cha_fx_alba, 20, sunriseFrame, FX_END_KEEP_LAST_FRAME},
  {"focus", &cha_fx_focus, 25, focusFrame, FX_END_RESTORE_AND_FLASH},
};

static float cur[3];                  // perceived level 0..1 per channel, as shown right now
static int lastDuty[3] = {-1, -1, -1};
static uint32_t lastRenderAt, dirtyAt;
static const FxDef *fx;               // running timed effect, or nullptr
static uint32_t fxStartedAt, fxDurationMs;
static Hsv fxLightBefore;
static Pulse pulse;
static uint32_t pulseStartedAt;
static bool isPulsing, isRaw, isDirty, isHomekitUp, isBootStable;

// ---------- boot-loop guard ----------
static void bootGuardWrite(BootGuard g) { ESP.rtcUserMemoryWrite(0, (uint32_t *)&g, sizeof g); }

static void bootGuardStart() {
  BootGuard g;
  ESP.rtcUserMemoryRead(0, (uint32_t *)&g, sizeof g);
  safeMode = bootGuardEnter(g, ESP.getResetInfoPtr()->reason);
  bootGuardWrite(g);
}

static void bootGuardTick() {
  if (isBootStable) return;
  uint32_t uptime = millis();
  bool isProven = uptime >= STABLE_MS && (isHomekitUp || safeMode);
  if (!isProven && uptime < STABLE_OFFLINE_MS) return;
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
  if (kvGet(text.c_str(), "tx", v, sizeof v)) cfg.txDbm = constrain(atoi(v), 0, MAX_TX_DBM);
  if (kvGet(text.c_str(), "shimmer", v, sizeof v)) cfg.shimmer = constrain(atoi(v), 0, 100);
  if (kvGet(text.c_str(), "hourly", v, sizeof v)) cfg.isHourly = v[0] == '1';
}

bool saveConfig() {
  char text[128];
  snprintf(text, sizeof text, "pins=%d,%d,%d\nphy=%c\nboot=%c\nfade=%u\nprov=%d\ntx=%u\nshimmer=%u\nhourly=%d\n",
           cfg.pins[0], cfg.pins[1], cfg.pins[2], cfg.phy, cfg.boot, cfg.fadeMs, cfg.provisioned,
           cfg.txDbm, cfg.shimmer, cfg.isHourly);
  return writeFile(CONFIG_PATH, text);
}

static void saveConfigOrLog() {
  if (!safeMode && !saveConfig()) LOG("could not save config");
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

static void markDirty() {
  isDirty = true;
  dirtyAt = millis();
}

// ---------- output ----------
static void writePwm() {
  for (int i = 0; i < 3; i++) {
    int duty = toDuty(cur[i]);
    if (duty == lastDuty[i]) continue;
    lastDuty[i] = duty;
    analogWrite(cfg.pins[i], duty);
  }
}

static void leaveRawMode() {
  isRaw = false;
  for (int &duty : lastDuty) duty = -1;  // the pins no longer show what we think
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
  leaveRawMode();
}

void setRaw(int r, int g, int b) {
  int duty[3] = {r, g, b};
  isRaw = true;
  for (int i = 0; i < 3; i++) analogWrite(cfg.pins[i], constrain(duty[i], 0, PWM_MAX));
}

static void syncSwitch(homekit_characteristic_t *ch, bool isOn) {
  ch->value.bool_value = isOn;
  if (isHomekitUp) homekit_characteristic_notify(ch, ch->value);
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

// ---------- effects ----------
static void startPulse(const Pulse &p) {
  pulse = p;
  pulseStartedAt = millis();
  isPulsing = true;
  leaveRawMode();
}

void flash(float hue, float saturation, int count) {
  startPulse(notifyPulse({clampf(hue, 0, 360), clampf(saturation, 0, 100), 100}, count));
}

void stopEffect() {
  if (!fx) return;
  syncSwitch(fx->homekitSwitch, false);
  fx = nullptr;
}

bool startEffect(const char *key, float minutes) {
  for (const FxDef &def : EFFECTS) {
    if (strcmp(def.key, key) != 0) continue;
    stopEffect();
    float duration = clampf(minutes > 0 ? minutes : def.defaultMinutes, MIN_EFFECT_MINUTES, MAX_EFFECT_MINUTES);
    fx = &def;
    fxStartedAt = millis();
    fxDurationMs = (uint32_t)(duration * 60000.0f);
    fxLightBefore = {light.h, light.s, light.on ? light.v : 0};
    leaveRawMode();
    syncSwitch(def.homekitSwitch, true);
    LOG("effect %s for %lus", def.key, (unsigned long)(fxDurationMs / 1000));
    return true;
  }
  return false;
}

static void finishEffect() {
  const FxDef *done = fx;
  float total = fxDurationMs / 1000.0f;
  Hsv last = done->frame(total, total, fxLightBefore);
  stopEffect();
  LOG("effect %s finished", done->key);
  if (done->end == FX_END_KEEP_LAST_FRAME) {  // the sunrise leaves the lamp on
    light.on = true;
    light.h = last.h;
    light.s = last.s;
    light.v = last.v;
    markDirty();
    notifyHomekit();
  } else {
    startPulse(notifyPulse(EFFECT_DONE_COLOUR, 3));
  }
}

const char *effectName() { return fx ? fx->key : ""; }

uint32_t effectSecondsLeft() {
  if (!fx) return 0;
  uint32_t elapsed = millis() - fxStartedAt;
  return elapsed >= fxDurationMs ? 0 : (fxDurationMs - elapsed) / 1000;
}

void setShimmer(int amount) {
  cfg.shimmer = constrain(amount, 0, 100);
  syncSwitch(&cha_shimmer, cfg.shimmer > 0);
  saveConfigOrLog();
}

void setHourly(bool isOn) {
  cfg.isHourly = isOn;
  syncSwitch(&cha_hourly, isOn);
  saveConfigOrLog();
}

// The lamp was changed by hand (Home app, web page, button): that wins over
// any running effect.
static void onLightChanged() {
  stopEffect();
  leaveRawMode();
  markDirty();
}

void applyLight() {
  onLightChanged();
  notifyHomekit();
}

static void renderTick() {
  uint32_t now = millis();
  uint32_t dt = now - lastRenderAt;
  if (isRaw || dt < RENDER_MS) return;
  lastRenderAt = now;

  Hsv want = {light.h, light.s, light.on ? light.v : 0};
  if (fx) {
    uint32_t elapsed = now - fxStartedAt;
    if (elapsed >= fxDurationMs) { finishEffect(); return; }
    want = fx->frame(elapsed / 1000.0f, fxDurationMs / 1000.0f, fxLightBefore);
  } else if (cfg.shimmer > 0) {
    want.v *= shimmerGain(now, cfg.shimmer / 100.0f);
  }

  bool isCrisp = false;  // pulses need hard edges: no smoothing
  if (isPulsing) {
    uint32_t elapsed = now - pulseStartedAt;
    if (elapsed >= pulseTotalMs(pulse)) {
      isPulsing = false;
    } else {
      isCrisp = true;
      if (pulseIsLit(pulse, elapsed)) want = pulse.colour;
      else want.v *= pulse.restGain;
    }
  }

  Rgb rgb = hsvToRgb(want.h, clampf(want.s, 0, 100), clampf(want.v, 0, 100));
  float target[3] = {rgb.r, rgb.g, rgb.b};
  for (int i = 0; i < 3; i++) cur[i] = isCrisp ? target[i] : smoothToward(cur[i], target[i], dt, cfg.fadeMs);
  writePwm();
}

static void blink(int times) {
  for (int i = 0; i < times; i++) {
    setRaw(0, 0, 0);
    delay(BLINK_MS);
    setRaw(PWM_MAX / 4, PWM_MAX / 4, PWM_MAX / 4);
    delay(BLINK_MS);
  }
  leaveRawMode();
}

// ---------- clock ----------
static void clockTick() {
  static uint32_t lastCheckAt = 0;
  static int lastHour = -1;
  if (millis() - lastCheckAt < 1000) return;
  lastCheckAt = millis();
  time_t now = time(nullptr);
  if (now < MIN_VALID_EPOCH) return;
  if (!isNewHour(lastHour, localtime(&now)->tm_hour)) return;
  // Only while the lamp is plainly on: not in the dark, not over an effect.
  if (cfg.isHourly && light.on && !fx) {
    startPulse(tickPulse());
    LOG("hour tick %02d:00", lastHour);
  }
}

// ---------- HomeKit ----------
// Setters come from the Home app: update and render, no echo back to HomeKit.
static void hkSetOn(const homekit_value_t v) { cha_on.value = v; light.on = v.bool_value; onLightChanged(); }
static void hkSetBri(const homekit_value_t v) { cha_bri.value = v; light.v = clampf(v.int_value, 0, 100); onLightChanged(); }
static void hkSetHue(const homekit_value_t v) { cha_hue.value = v; light.h = clampf(v.float_value, 0, 360); onLightChanged(); }
static void hkSetSat(const homekit_value_t v) { cha_sat.value = v; light.s = clampf(v.float_value, 0, 100); onLightChanged(); }

static void hkSetEffect(homekit_characteristic_t *ch, const homekit_value_t v) {
  ch->value = v;
  for (const FxDef &def : EFFECTS) {
    if (def.homekitSwitch != ch) continue;
    if (v.bool_value) startEffect(def.key, 0);
    else if (fx == &def) stopEffect();
  }
}

static void hkSetShimmer(const homekit_value_t v) {
  bool wantsOn = v.bool_value;
  setShimmer(wantsOn ? (cfg.shimmer ? cfg.shimmer : DEFAULT_SHIMMER) : 0);
}

static void hkSetHourly(const homekit_value_t v) { setHourly(v.bool_value); }

static void hkEvent(homekit_event_t event) {
  if (event == HOMEKIT_EVENT_PAIRING_ADDED) LOG("homekit: paired");
  if (event == HOMEKIT_EVENT_PAIRING_REMOVED) LOG("homekit: pairing removed");
}

static void homekitStart() {
  cha_on.setter = hkSetOn;
  cha_bri.setter = hkSetBri;
  cha_hue.setter = hkSetHue;
  cha_sat.setter = hkSetSat;
  for (const FxDef &def : EFFECTS) def.homekitSwitch->setter_ex = hkSetEffect;
  cha_shimmer.setter = hkSetShimmer;
  cha_hourly.setter = hkSetHourly;
  cha_shimmer.value.bool_value = cfg.shimmer > 0;
  cha_hourly.value.bool_value = cfg.isHourly;
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
    // Three unrequested reboots in a row: skip flash storage and HomeKit, keep
    // the lamp, the web page and /update so a fixed firmware can be uploaded.
    LOG("SAFE MODE: storage and HomeKit skipped, upload a firmware at /update");
  } else {
    if (!LittleFS.begin()) LOG("storage mount failed, using defaults");
    loadConfig();
    loadState();
  }
  claimPins();  // the render loop fades the lamp in before Wi-Fi is up
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
  clockTick();
  renderTick();
  if (isDirty && !safeMode && millis() - dirtyAt > SAVE_DELAY_MS) {
    isDirty = false;
    saveState();
  }
}
