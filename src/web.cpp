// HTTP API and the one-page control UI. Reading and setting the light is open
// on the LAN; anything that reconfigures or reflashes needs the password.
#include <ESP8266HTTPUpdateServer.h>
#include <ESP8266WebServer.h>
#include <time.h>
#include "app.h"

static ESP8266WebServer server(80);
static ESP8266HTTPUpdateServer updater;

static const char PAGE[] PROGMEM = R"html(<!doctype html><html lang=it><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>specchio</title>
<style>
:root{color-scheme:dark}
body{margin:0;background:#111;color:#eee;font:16px/1.4 -apple-system,system-ui,sans-serif}
main{max-width:380px;margin:0 auto;padding:28px 20px}
h1{font-size:22px;font-weight:600;margin:0 0 20px}
button{font:inherit;width:100%;padding:16px;border:0;border-radius:14px;background:#2a2a2a;color:#eee}
button.on{background:#f5c451;color:#111}
input{width:100%;margin:18px 0 0;box-sizing:border-box}
input[type=checkbox]{width:auto;margin:0 8px 0 0}
.r{display:flex;gap:10px;margin-top:18px}label{display:block;margin-top:18px;color:#888;font-size:14px}
input:not([type]),input[type=password]{font:inherit;padding:12px;border:0;border-radius:10px;background:#2a2a2a;color:#eee}
details{margin:18px 0;color:#888;font-size:14px}details button{margin-top:18px}
input[type=color]{height:72px;border:0;border-radius:14px;padding:0;background:none}
p{color:#888;font-size:14px;font-variant-numeric:tabular-nums}
a{color:#888}
</style><main><h1>specchio</h1>
<button id=p>…</button>
<input type=color id=c aria-label=Colore>
<input type=range id=v min=1 max=100 aria-label=Luminosità>
<div class=r><button id=fa>Alba</button><button id=ff>Focus</button></div>
<label>Vibrazione<input type=range id=m min=0 max=100></label>
<label><input type=checkbox id=o>Tic a ogni cambio d'ora</label>
<label><input type=checkbox id=q>Lampeggia quando Claude finisce</label>
<label>Sveglia: l'alba parte prima, la luce insiste dopo<input type=time id=w></label>
<p>Segnale <span id=g>…</span><br><span id=k></span></p>
<details><summary>Cambia rete Wi-Fi</summary><form method=post action=/api/wifi>
<input name=ssid placeholder="Nome rete" required maxlength=32 autocapitalize=none>
<input name=pass type=password placeholder=Password maxlength=63>
<button>Salva e riavvia</button><p>Chiede utente (admin) e password del firmware.</p></form></details>
<p><a href=/api/log>registro</a> · <a href=/api/state>stato</a> · <a href=/update>aggiorna firmware</a></p>
</main><script>
const $=i=>document.getElementById(i);let st={},timer=0,touched=0;
function show(j){st=j;$('p').textContent=j.on?'Accesa':'Spenta';$('p').className=j.on?'on':'';
if(Date.now()-touched>1500){$('c').value='#'+j.hex;$('v').value=j.v;$('m').value=j.shimmer;$('o').checked=j.hourly;$('q').checked=j.claude;
if(document.activeElement!=$('w'))$('w').value=j.wake}
for(const[b,n,t]of[['fa','alba','Alba'],['ff','focus','Focus']]){const a=j.effect==n;$(b).className=a?'on':'';
$(b).textContent=a?t+' · '+Math.ceil(j.effect_left_s/60)+' min':t}
$('g').textContent=j.rssi?j.rssi+' dBm, '+(j.rssi>-67?'buono':j.rssi>-80?'debole':'molto debole'):'non collegata al Wi-Fi';
$('k').textContent=j.safe_mode?'MODALITÀ SICURA: carica un firmware':j.homekit_paired?'HomeKit associato':'HomeKit da associare'}
async function set(q){touched=Date.now();show(await(await fetch('/api/set?'+q)).json())}
function soon(q){touched=Date.now();clearTimeout(timer);timer=setTimeout(()=>set(q),120)}
$('p').onclick=()=>set('on='+(st.on?0:1));
$('c').oninput=e=>soon('hex='+e.target.value.slice(1));
$('v').oninput=e=>soon('v='+e.target.value);
$('m').oninput=e=>soon('shimmer='+e.target.value);
$('o').onchange=e=>set('hourly='+(e.target.checked?1:0));
$('q').onchange=e=>set('claude='+(e.target.checked?1:0));
$('w').onchange=e=>fetch('/api/wake?'+(e.target.value?'at='+e.target.value:'off=1')).then(poll);
const fx=n=>fetch('/api/effect?name='+(st.effect==n?'off':n)).then(poll);
$('fa').onclick=()=>fx('alba');$('ff').onclick=()=>fx('focus');
async function poll(){try{show(await(await fetch('/api/state')).json())}catch(e){}}
poll();setInterval(poll,2000)
</script>)html";

static bool isAuthorized() {
  if (server.authenticate(OTA_USER, OTA_PASS)) return true;
  server.requestAuthentication();
  return false;
}

static void sendText(int code, const char *text) { server.send(code, "text/plain", text); }

// SSIDs are user-chosen text: keep the JSON valid whatever they contain.
static String jsonSafe(String s) {
  for (size_t i = 0; i < s.length(); i++) {
    if (s[i] == '"' || s[i] == '\\' || (uint8_t)s[i] < 0x20) s[i] = '?';
  }
  return s;
}

static void sendState() {
  Rgb c = hsvToRgb(light.h, light.s, 100);
  char hex[7];
  snprintf(hex, sizeof hex, "%02x%02x%02x", (int)lroundf(c.r * 255), (int)lroundf(c.g * 255),
           (int)lroundf(c.b * 255));
  bool isUp = WiFi.status() == WL_CONNECTED;
  char clock[6] = "";
  time_t now = time(nullptr);
  if (now > 1700000000) strftime(clock, sizeof clock, "%H:%M", localtime(&now));
  char wake[6] = "";
  time_t wakeAt = cfg.wakeAt;
  if (wakeAt) strftime(wake, sizeof wake, "%H:%M", localtime(&wakeAt));
  char scenes[80];
  scenesOn(scenes, sizeof scenes);
  char buf[1024];
  snprintf(buf, sizeof buf,
    "{\"on\":%s,\"h\":%s,\"s\":%s,\"v\":%d,\"hex\":\"%s\","
    "\"rssi\":%d,\"ssid\":\"%s\",\"bssid\":\"%s\",\"channel\":%d,\"phy\":\"%c\","
    "\"disconnects\":%lu,\"failed_joins\":%lu,\"last_disconnect\":\"%s\",\"tx_dbm\":%u,"
    "\"pins\":\"%d,%d,%d\",\"boot\":\"%c\",\"fade_ms\":%u,"
    "\"effect\":\"%s\",\"effect_left_s\":%lu,\"shimmer\":%u,\"hourly\":%s,\"claude\":%s,\"scenes\":\"%s\",\"time\":\"%s\","
    "\"wake\":\"%s\",\"wake_phase\":\"%s\",\"wake_lead_min\":%u,\"wake_insist_min\":%u,"
    "\"homekit_paired\":%s,\"homekit_clients\":%d,\"safe_mode\":%s,"
    "\"heap\":%lu,\"uptime_s\":%lu,\"reset\":\"%s\",\"fw\":\"%s\"}",
    light.on ? "true" : "false", String(light.h, 1).c_str(), String(light.s, 1).c_str(), (int)light.v, hex,
    isUp ? (int)WiFi.RSSI() : 0, jsonSafe(WiFi.SSID()).c_str(), isUp ? WiFi.BSSIDstr().c_str() : "",
    isUp ? (int)WiFi.channel() : 0, cfg.phy,
    (unsigned long)netDisconnects, (unsigned long)netFailedJoins,
    netLastReason ? discReasonName(netLastReason) : "", cfg.txDbm,
    cfg.pins[0], cfg.pins[1], cfg.pins[2], cfg.boot, cfg.fadeMs,
    effectName(), (unsigned long)effectSecondsLeft(), cfg.shimmer, cfg.isHourly ? "true" : "false",
    cfg.isClaudeOn ? "true" : "false", scenes, clock,
    wake, wakePhaseName(), cfg.wakeLeadMin, cfg.wakeInsistMin,
    homekitPaired() ? "true" : "false", homekitClients(), safeMode ? "true" : "false",
    (unsigned long)ESP.getFreeHeap(), millis() / 1000UL, ESP.getResetReason().c_str(), FW_VERSION);
  server.send(200, "application/json", buf);
#if defined(SELF_RESTORE) && !defined(GUARD_SELFTEST)
  // Answered someone on the home network (not on our own setup AP): reachable.
  if (server.client().localIP() != WiFi.softAPIP()) guardReached();
#endif
}

static void handleSet() {
  if (server.hasArg("on")) light.on = server.arg("on") != "0";
  if (server.hasArg("hex")) {
    float h, s, v;
    if (!hexToHsv(server.arg("hex").c_str(), h, s, v)) return sendText(400, "hex: 6 hex digits, e.g. ff8000");
    light.h = h;
    light.s = s;
    light.on = true;
  }
  if (server.hasArg("h")) light.h = clampf(server.arg("h").toFloat(), 0, 360);
  if (server.hasArg("s")) light.s = clampf(server.arg("s").toFloat(), 0, 100);
  if (server.hasArg("v")) light.v = clampf(server.arg("v").toFloat(), 1, 100);
  if (server.hasArg("shimmer")) setShimmer(server.arg("shimmer").toInt());
  if (server.hasArg("hourly")) setHourly(server.arg("hourly") != "0");
  if (server.hasArg("claude")) setClaude(server.arg("claude") != "0");
  // Shimmer and the hourly tick are settings: changing only those must not
  // cancel a running effect the way touching the lamp itself does.
  bool isLightChange = server.hasArg("on") || server.hasArg("hex") || server.hasArg("h") ||
                       server.hasArg("s") || server.hasArg("v");
  if (isLightChange) applyLight();
  sendState();
}

// /api/scene?name=temporale&on=1   flips the Home switch of a scene the Mac plays (see SCENES in main.cpp)
static void handleScene() {
  if (!setScene(server.arg("name").c_str(), server.arg("on") != "0")) {
    return sendText(400, "name: temporale, aria, terremoti, posto, aerei, mare, luogo, sole or sonno");
  }
  sendState();
}

// /api/effect?name=alba&minutes=20   name=off stops whatever is running
static void handleEffect() {
  String name = server.arg("name");
  if (name == "off") { stopEffect(); return sendState(); }
  float minutes = server.arg("minutes").toFloat();  // 0 when absent: use the effect's default
  if (server.hasArg("minutes") && !(minutes >= MIN_EFFECT_MINUTES && minutes <= MAX_EFFECT_MINUTES)) {
    return sendText(400, "minutes: 0.1 to 180");
  }
  if (!startEffect(name.c_str(), minutes)) return sendText(400, "name: alba, focus or off");
  sendState();
}

// /api/wake?at=07:30[&lead=30][&insist=10]   next time the clock reads 07:30
// /api/wake?stop=1   "I am up": ends a wake-up in progress, lamp stays on
// /api/wake?off=1    forget the alarm
// /api/wake            the phone's alarms: ?at=07:30, ?at=07:00,07:30, or a POST
//                      with any text that has times in it; the soonest is kept,
//                      and an empty list means "no alarm any more"
//   ?ring=1            an app that cannot tell its alarm in advance: ringing now
//   ?stop=1            "I am up": the sequence stops, the lamp stays on
//   ?off=1             forget the alarm, leave the lamp alone
//   &lead= &insist=    minutes of sunrise before, minutes before the blinking
static void handleWake() {
  if (server.arg("stop") == "1") { acknowledgeWake(); return sendState(); }
  if (server.arg("off") == "1") { cancelWake(); return sendState(); }
  long lead = server.hasArg("lead") ? server.arg("lead").toInt() : cfg.wakeLeadMin;
  long insist = server.hasArg("insist") ? server.arg("insist").toInt() : cfg.wakeInsistMin;
  if (lead < MIN_WAKE_LEAD_MIN || lead > MAX_WAKE_LEAD_MIN) return sendText(400, "lead: 1-60 minutes");
  if (insist < 0 || insist > MAX_WAKE_INSIST_MIN) return sendText(400, "insist: 0-60 minutes, 0 = never");

  time_t now = time(nullptr);
  int hour, minute;
  if (server.arg("ring") == "1") {
    time_t soon = now + 60;  // the coming minute: the sunrise is already under way
    struct tm *at = localtime(&soon);
    hour = at->tm_hour;
    minute = at->tm_min;
    lead = MIN_WAKE_LEAD_MIN;
  } else {
    // Whatever the phone sent, in whichever field: query, form or raw body.
    String sent;
    for (int i = 0; i < server.args(); i++) {
      const String &name = server.argName(i);
      if (name != "lead" && name != "insist" && name != "src") sent += server.arg(i) + ' ';
    }
    sent.trim();
    bool isSync = server.hasArg("at") || server.method() == HTTP_POST;
    if (sent.isEmpty() && isSync) { cancelWake(); return sendState(); }
    struct tm *local = localtime(&now);
    if (!soonestClock(sent.c_str(), local->tm_hour, local->tm_min, local->tm_sec, hour, minute)) {
      return sendText(400, "no time found: send at=HH:MM, several, or any text with times in it (or stop=1, off=1, ring=1)");
    }
  }
  if (!setWake(hour, minute, lead, insist)) return sendText(503, "the strip has not got the time from the network yet");
  sendState();
}

// /api/flash?hex=ff8000&n=3   coloured blinks, then back to what was showing
//   &src=claude               sent when Claude finishes: only shown while the "Claude" switch is on
static void handleFlash() {
  if (server.arg("src") == "claude" && !cfg.isClaudeOn) return sendText(200, "muted: the Claude switch is off");
  float h, s, v;
  if (!hexToHsv(server.arg("hex").c_str(), h, s, v)) return sendText(400, "hex: 6 hex digits, e.g. ff8000");
  long count = server.hasArg("n") ? server.arg("n").toInt() : 3;
  if (count < 1 || count > 10) return sendText(400, "n: 1 to 10");
  flash(h, s, count);
  sendText(200, "ok");
}

// Drives the three outputs directly: used to find which GPIO is which colour.
static void handleRaw() {
  int r = server.arg("r").toInt(), g = server.arg("g").toInt(), b = server.arg("b").toInt();
  if (!setRaw(r, g, b)) return sendText(409, "wake-up running: the lamp is taken");
  char buf[64];
  snprintf(buf, sizeof buf, "{\"raw\":[%d,%d,%d]}", constrain(r, 0, PWM_MAX), constrain(g, 0, PWM_MAX),
           constrain(b, 0, PWM_MAX));
  server.send(200, "application/json", buf);
}

static void handleConfig() {
  if (!isAuthorized()) return;
  static const struct { const char *name; char code; } bootModes[] = {{"last", 'l'}, {"on", '1'}, {"off", '0'}};

  // Validate everything first so a bad request changes nothing.
  int pins[3];
  bool hasPins = server.hasArg("pins");
  if (hasPins && !parsePins(server.arg("pins").c_str(), pins)) return sendText(400, "pins: three of 4,5,12,13,14,15");
  String phy = server.arg("phy");
  if (server.hasArg("phy") && (phy.length() != 1 || !isPhy(phy[0]))) return sendText(400, "phy: b, g or n");
  char boot = '\0';
  for (const auto &m : bootModes) if (server.arg("boot") == m.name) boot = m.code;
  if (server.hasArg("boot") && !boot) return sendText(400, "boot: last, on or off");
  long fade = server.arg("fade").toInt();
  if (server.hasArg("fade") && (fade < 0 || fade > 5000)) return sendText(400, "fade: 0-5000 ms");
  long tx = server.arg("tx").toInt();
  if (server.hasArg("tx") && (tx < 0 || tx > MAX_TX_DBM)) return sendText(400, "tx: 0 (default) to 20 dBm");

  if (hasPins) setPins(pins);
  if (server.hasArg("phy")) { cfg.phy = phy[0]; LOG("phy set to %c, applies after reboot", cfg.phy); }
  if (boot) cfg.boot = boot;
  if (server.hasArg("fade")) cfg.fadeMs = fade;
  if (server.hasArg("tx")) { cfg.txDbm = tx; LOG("tx power set to %ld dBm, applies after reboot", tx); }
  if (!safeMode && !saveConfig()) return sendText(500, "could not write config to flash");
  sendState();
}

static void handleWifi() {
  if (!isAuthorized()) return;
  String ssid = server.arg("ssid"), pass = server.arg("pass");
  if (ssid.isEmpty() || ssid.length() > 32) return sendText(400, "ssid: 1-32 characters");
  if (pass.length() > 63 || (pass.length() > 0 && pass.length() < 8)) return sendText(400, "pass: 8-63 characters, or empty for an open network");
  sendText(200, "saved, rebooting");
  delay(200);
  WiFi.persistent(true);
  WiFi.begin(ssid.c_str(), pass.c_str());  // stored by the SDK
  restartClean();
}

static void handleLog() {
  String out;
  out.reserve(elog.size() * 48);
  for (int i = 0; i < elog.size(); i++) { out += elog.line(i); out += '\n'; }
  server.send(200, "text/plain; charset=utf-8", out);
}

// /api/diag   what the radio and the flash look like right now. Runs only when
// asked: the scan blocks for about 3 s and drops a join in progress.
static void handleDiag() {
  // ?cc=1   live test on a boot that cannot see the network: force the radio
  // to channels 1-13 and try to join again.
  if (server.arg("cc") == "1") {
    wifi_country_t forced = {"IT", 1, 13, WIFI_COUNTRY_POLICY_MANUAL};
    bool isSet = wifi_set_country(&forced);
    WiFi.begin();
    return sendText(200, isSet ? "channels forced to 1-13, joining" : "wifi_set_country failed");
  }
  String out;
  out.reserve(900);
  char line[220];
  wifi_country_t country = {};
  bool hasCountry = wifi_get_country(&country);
  struct station_config sta = {};
  wifi_station_get_config(&sta);
  snprintf(line, sizeof line,
           "fw=%s\ncountry=%s %.2s ch%u+%u policy%u\nopmode=%u phy=%u sta_status=%u\n"
           "sta_ssid=%.32s bssid_set=%u bssid=%02x:%02x:%02x:%02x:%02x:%02x\n",
           FW_VERSION, hasCountry ? "ok" : "unreadable", country.cc, country.schan, country.nchan, country.policy,
           wifi_get_opmode(), wifi_get_phy_mode(), wifi_station_get_connect_status(),
           (const char *)sta.ssid, sta.bssid_set,
           sta.bssid[0], sta.bssid[1], sta.bssid[2], sta.bssid[3], sta.bssid[4], sta.bssid[5]);
  out += line;
  snprintf(line, sizeof line, "flash_id=%06x real=%u header=%u map=%u sketch=%u free=%u crc_ok=%d\nsketch_md5=%s\n",
           (unsigned)ESP.getFlashChipId(), (unsigned)ESP.getFlashChipRealSize(), (unsigned)ESP.getFlashChipSize(),
           (unsigned)system_get_flash_size_map(), (unsigned)ESP.getSketchSize(), (unsigned)ESP.getFreeSketchSpace(),
           ESP.checkFlashCRC(), ESP.getSketchMD5().c_str());
  out += line;
  int found = WiFi.scanNetworks(false, true);
  snprintf(line, sizeof line, "scan=%d\n", found);
  out += line;
  for (int i = 0; i < found; i++) {
    snprintf(line, sizeof line, "ch%d %ddBm %s\n", (int)WiFi.channel(i), (int)WiFi.RSSI(i), WiFi.SSID(i).c_str());
    out += line;
  }
  WiFi.scanDelete();
  server.send(200, "text/plain; charset=utf-8", out);
}

void webBegin() {
  server.on("/api/diag", handleDiag);
  server.on("/", [] { server.send_P(200, "text/html; charset=utf-8", PAGE); });
  server.on("/api/state", sendState);
  server.on("/api/log", handleLog);
  server.on("/api/set", handleSet);
  server.on("/api/raw", handleRaw);
  server.on("/api/effect", handleEffect);
  server.on("/api/scene", handleScene);
  server.on("/api/flash", handleFlash);
  server.on("/api/wake", handleWake);
  server.on("/api/spin", [] {  // ?s=30 starts, ?s=0 stops, no argument just reports; see LOOP_REST_MS
    if (server.hasArg("s")) spinFor(server.arg("s").toInt() > 0 ? server.arg("s").toInt() : 0);
    String text = "loop_hz=" + String(loopsPerSecond()) + " uptime_s=" + String(millis() / 1000) +
                  " homekit_clients=" + String(homekitClients()) + "\n" + stallReport();
    sendText(200, text.c_str());
  });
  server.on("/api/roam", [] { netRequestRoamScan(); sendText(200, "scan started, see /api/log"); });
  server.on("/api/config", handleConfig);
  server.on("/api/wifi", HTTP_POST, handleWifi);
  server.on("/api/reboot", HTTP_POST, [] {
    if (!isAuthorized()) return;
    sendText(200, "rebooting");
    restartClean();
  });
  server.on("/api/factory-reset", HTTP_POST, [] {
    if (!isAuthorized()) return;
    sendText(200, "erasing HomeKit pairing and settings, rebooting");
    delay(200);
    factoryReset();
  });
#ifdef SELF_RESTORE
  void guardStashChunk(HTTPUpload &up);  // guard.cpp; needs the web server's types
  // The known-good image the guard falls back to: upload it, then read it back.
  auto describeStash = [] {
    char text[200];
    guardDescribe(text, sizeof text);
    server.send(200, String(F("text/plain")), String(text));
  };
  server.on(String(F("/api/stash")), HTTP_GET, describeStash);
  server.on(String(F("/api/stash")), HTTP_POST, describeStash, [] {
    if (server.authenticate(OTA_USER, OTA_PASS)) guardStashChunk(server.upload());
  });
  server.on(String(F("/api/restore")), HTTP_POST, [] {
    if (!isAuthorized()) return;
    int failedAt = guardRestore();
    char text[200];
    guardDescribe(text, sizeof text);
    server.send(200, String(F("text/plain")), String(text));
    delay(300);
    if (failedAt == 0) ESP.restart();  // not restartClean(): it would erase the bootloader's command
  });
#endif
  updater.setup(&server, "/update", OTA_USER, OTA_PASS);
  server.begin();
}

void webTick() {
  server.handleClient();
  // The updater stops every UDP socket when an upload starts, HomeKit's mDNS
  // included. If the upload then fails nothing brings mDNS back, and the Home
  // app loses the strip: a reboot is the simplest way to restore it.
  if (Update.hasError() && !Update.isRunning()) {
    LOG("firmware upload failed (error %d), rebooting", (int)Update.getError());
    restartClean();
  }
}
