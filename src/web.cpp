// HTTP API and the one-page control UI. Reading and setting the light is open
// on the LAN; anything that reconfigures or reflashes needs the password.
#include <ESP8266HTTPUpdateServer.h>
#include <ESP8266WebServer.h>
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
input{width:100%;margin:18px 0 0}
input[type=color]{height:72px;border:0;border-radius:14px;padding:0;background:none}
p{color:#888;font-size:14px;font-variant-numeric:tabular-nums}
a{color:#888}
</style><main><h1>specchio</h1>
<button id=p>…</button>
<input type=color id=c aria-label=Colore>
<input type=range id=v min=1 max=100 aria-label=Luminosità>
<p>Segnale <span id=g>…</span><br><span id=k></span></p>
<p><a href=/api/log>registro</a> · <a href=/api/state>stato</a> · <a href=/update>aggiorna firmware</a></p>
</main><script>
const $=i=>document.getElementById(i);let st={},timer=0,touched=0;
function show(j){st=j;$('p').textContent=j.on?'Accesa':'Spenta';$('p').className=j.on?'on':'';
if(Date.now()-touched>1500){$('c').value='#'+j.hex;$('v').value=j.v}
$('g').textContent=j.rssi+' dBm, '+(j.rssi>-67?'buono':j.rssi>-80?'debole':'molto debole');
$('k').textContent=j.safe_mode?'MODALITÀ SICURA: carica un firmware':j.homekit_paired?'HomeKit associato':'HomeKit da associare'}
async function set(q){touched=Date.now();show(await(await fetch('/api/set?'+q)).json())}
function soon(q){touched=Date.now();clearTimeout(timer);timer=setTimeout(()=>set(q),120)}
$('p').onclick=()=>set('on='+(st.on?0:1));
$('c').oninput=e=>soon('hex='+e.target.value.slice(1));
$('v').oninput=e=>soon('v='+e.target.value);
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
  char buf[720];
  snprintf(buf, sizeof buf,
    "{\"on\":%s,\"h\":%s,\"s\":%s,\"v\":%d,\"hex\":\"%s\","
    "\"rssi\":%d,\"ssid\":\"%s\",\"bssid\":\"%s\",\"channel\":%d,\"phy\":\"%c\","
    "\"disconnects\":%lu,\"last_disconnect\":\"%s\","
    "\"pins\":\"%d,%d,%d\",\"boot\":\"%c\",\"fade_ms\":%u,"
    "\"homekit_paired\":%s,\"homekit_clients\":%d,\"safe_mode\":%s,"
    "\"heap\":%lu,\"uptime_s\":%lu,\"reset\":\"%s\",\"fw\":\"%s\"}",
    light.on ? "true" : "false", String(light.h, 1).c_str(), String(light.s, 1).c_str(), (int)light.v, hex,
    isUp ? (int)WiFi.RSSI() : 0, jsonSafe(WiFi.SSID()).c_str(), isUp ? WiFi.BSSIDstr().c_str() : "",
    isUp ? (int)WiFi.channel() : 0, cfg.phy,
    (unsigned long)netDisconnects, netDisconnects ? discReasonName(netLastReason) : "",
    cfg.pins[0], cfg.pins[1], cfg.pins[2], cfg.boot, cfg.fadeMs,
    homekitPaired() ? "true" : "false", homekitClients(), safeMode ? "true" : "false",
    (unsigned long)ESP.getFreeHeap(), millis() / 1000UL, ESP.getResetReason().c_str(), FW_VERSION);
  server.send(200, "application/json", buf);
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
  applyLight();
  sendState();
}

// Drives the three outputs directly: used to find which GPIO is which colour.
static void handleRaw() {
  int r = server.arg("r").toInt(), g = server.arg("g").toInt(), b = server.arg("b").toInt();
  setRaw(r, g, b);
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

  if (hasPins) setPins(pins);
  if (server.hasArg("phy")) { cfg.phy = phy[0]; LOG("phy set to %c, applies after reboot", cfg.phy); }
  if (boot) cfg.boot = boot;
  if (server.hasArg("fade")) cfg.fadeMs = fade;
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

void webBegin() {
  server.on("/", [] { server.send_P(200, "text/html; charset=utf-8", PAGE); });
  server.on("/api/state", sendState);
  server.on("/api/log", handleLog);
  server.on("/api/set", handleSet);
  server.on("/api/raw", handleRaw);
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
