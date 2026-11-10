// Wi-Fi link for a strip that sits at the edge of coverage (-89 dBm).
//
// Lessons from the first flash (docs/first-flash-log.txt): at this signal the
// link drops within a minute of boot and a reconnect can take four minutes.
// So: the SDK's own continuous reconnect is never switched off just because
// we are offline, the setup AP waits five minutes, and the radio settings
// stay at the stock firmware's values unless changed through /api/config.
//
// Rule for this file: the credentials saved in flash are only ever written by
// an explicit user action or after a connection with them has succeeded.
// Every experiment (fallback credentials, pinning an AP) is volatile.
#include <ESP8266mDNS.h>
#include "app.h"
extern "C" {
#include <user_interface.h>
}

static const uint32_t FALLBACK_AFTER_MS = 30000;    // try compiled-in credentials
static const uint32_t RESTORE_AFTER_MS = 60000;     // give up on them, widen the PHY mode
static const uint32_t SETUP_AP_AFTER_MS = 5UL * 60 * 1000;
static const uint32_t SETUP_AP_RETRY_MS = 30000;    // if the AP itself fails to start
static const uint32_t SETUP_AP_MIN_UP_MS = 5UL * 60 * 1000;
static const uint32_t SETUP_PAUSE_MAX_MS = 5UL * 60 * 1000;  // longest we stay quiet for a setup-page user
static const uint32_t RECONNECT_KICK_MS = 5UL * 60 * 1000;   // belt and braces on top of SDK auto-reconnect
static const uint32_t UNPIN_AFTER_MS = 20000;
static const uint32_t ROAM_CHECK_MS = 30UL * 60 * 1000;

static_assert(sizeof(OTA_PASS) - 1 >= 8 && sizeof(OTA_PASS) - 1 <= 63,
              "OTA_PASS is also the setup AP key: WPA2 needs 8-63 characters");

uint32_t netDisconnects = 0;
uint32_t netFailedJoins = 0;
int netLastReason = 0;

static WiFiEventHandler onGotIp, onDisconnected;
static uint32_t offlineSince, lastRoamCheck, lastKick, apStartedAt, lastApAttempt, apClientSince;
static char scanSsid[33];
static bool isLinkUp, isApUp, isReconnectPaused, isScanning, isRoamRequested;
static bool isPinnedToAp, isOnFallback, hasSavedCredentials;
static bool hasTriedFallback, hasRestored, hasStartedMdns;

static WiFiPhyMode_t phyMode(char c) {
  return c == 'b' ? WIFI_PHY_MODE_11B : c == 'g' ? WIFI_PHY_MODE_11G : WIFI_PHY_MODE_11N;
}

// Connect without touching the credentials saved in flash.
static void connectVolatile(const char *ssid, const String &psk, int channel, const uint8_t *bssid,
                            bool dropCurrentLink) {
  WiFi.persistent(false);
  WiFi.begin(ssid, psk.c_str(), channel, bssid, !dropCurrentLink);
  WiFi.persistent(true);
  // While associated the SDK ignores a plain connect: it has to leave first.
  // reconnect() does that without erasing anything (disconnect() would).
  if (dropCurrentLink) WiFi.reconnect();
}

static void useSavedCredentials() {
  struct station_config saved;
  if (!wifi_station_get_config_default(&saved) || saved.ssid[0] == '\0') return;
  wifi_station_disconnect();  // the SDK ignores connect() while it is still trying the old config
  wifi_station_set_config_current(&saved);
  wifi_station_connect();
}

static void setReconnectPaused(bool shouldPause) {
  if (shouldPause == isReconnectPaused) return;
  isReconnectPaused = shouldPause;
  WiFi.setAutoReconnect(!shouldPause);
  if (!shouldPause) WiFi.begin();
  LOG(shouldPause ? "someone is on the setup AP: Wi-Fi retries paused" : "Wi-Fi retries resumed");
}

void netStartSetupAp() {
  uint32_t now = millis();
  apStartedAt = now;  // a repeated request keeps an already running AP alive
  if (isApUp) return;
  lastApAttempt = now;
  WiFi.persistent(false);
  bool ok = WiFi.mode(WIFI_AP_STA) && WiFi.softAP(SETUP_AP_SSID, OTA_PASS);
  if (!ok) WiFi.mode(WIFI_STA);  // never leave the SDK's default open AP on air
  WiFi.persistent(true);
  if (!ok) { LOG("setup AP failed to start"); return; }
  isApUp = true;
  LOG("setup AP up: " SETUP_AP_SSID " -> http://192.168.4.1");
}

static void stopSetupAp() {
  WiFi.persistent(false);
  WiFi.softAPdisconnect(true);
  WiFi.persistent(true);
  isApUp = false;
  LOG("setup AP down");
}

void netRequestRoamScan() { isRoamRequested = true; }

static void finishRoamScan(int found) {
  int best = -1;
  for (int i = 0; i < found; i++) {
    if (WiFi.SSID(i) == scanSsid && (best < 0 || WiFi.RSSI(i) > WiFi.RSSI(best))) best = i;
  }
  if (best < 0) { LOG("roam scan: network not seen"); return; }

  int current = WiFi.RSSI();
  bool isSameAp = memcmp(WiFi.BSSID(best), WiFi.BSSID(), 6) == 0;
  LOG("roam scan: %d AP, best %s ch%d %ddBm, now %ddBm", found,
      WiFi.BSSIDstr(best).c_str(), (int)WiFi.channel(best), (int)WiFi.RSSI(best), current);
  if (!shouldRoam(current, WiFi.RSSI(best), isSameAp)) return;

  uint8_t bssid[6];
  memcpy(bssid, WiFi.BSSID(best), 6);
  int channel = WiFi.channel(best);
  LOG("roaming to %s", WiFi.BSSIDstr(best).c_str());
  isLinkUp = false;  // the drop that follows is ours: not a lost link
  connectVolatile(scanSsid, WiFi.psk(), channel, bssid, true);
  isPinnedToAp = true;
}

static void roamTick(uint32_t now) {
  if (isScanning) {
    int found = WiFi.scanComplete();
    if (found == WIFI_SCAN_RUNNING) return;
    isScanning = false;
    if (found >= 0) finishRoamScan(found);
    WiFi.scanDelete();
    return;
  }
  bool isDue = now - lastRoamCheck >= ROAM_CHECK_MS && WiFi.RSSI() <= ROAM_BELOW_DBM;
  if (!isRoamRequested && !isDue) return;
  isRoamRequested = false;
  lastRoamCheck = now;
  strlcpy(scanSsid, WiFi.SSID().c_str(), sizeof scanSsid);  // the SDK keeps this pointer during the scan
  isScanning = WiFi.scanNetworks(true, false, 0, (uint8 *)scanSsid) == WIFI_SCAN_RUNNING;
}

static void onlineTick(uint32_t now) {
  offlineSince = lastKick = now;
  hasTriedFallback = hasRestored = false;
  setReconnectPaused(false);
  if (isOnFallback) {  // they work: now, and only now, make them the saved ones
    isOnFallback = false;
    hasSavedCredentials = true;
    LOG("built-in Wi-Fi credentials work, saving them");
    WiFi.begin(WIFI_SSID, WIFI_PASS);
  }
  if (!cfg.provisioned && !safeMode) {
    cfg.provisioned = true;
    if (!saveConfig()) LOG("could not save config");
  }
  bool isApIdle = now - apStartedAt > SETUP_AP_MIN_UP_MS && WiFi.softAPgetStationNum() == 0;
  if (isApUp && !safeMode && isApIdle) stopSetupAp();
  if (safeMode && !hasStartedMdns) hasStartedMdns = MDNS.begin(HOSTNAME);  // HomeKit does this otherwise
  if (hasStartedMdns) MDNS.update();
  roamTick(now);
}

static void offlineTick(uint32_t now) {
  uint32_t offlineMs = now - offlineSince;

  if (isPinnedToAp && offlineMs > UNPIN_AFTER_MS) {  // the AP we roamed to is gone
    isPinnedToAp = false;
    LOG("pinned AP lost, back to any AP");
    useSavedCredentials();
  }
  // Compiled-in credentials: only before the first successful join with this
  // firmware, so a network chosen later by the user is never second-guessed.
  // Safe mode cannot read `provisioned` (storage is skipped), so there the
  // fallback is allowed only when nothing is saved at all.
  bool hasFallback = WIFI_SSID[0] != '\0';
  bool canUseFallback = hasFallback && !cfg.provisioned && !hasTriedFallback;
  bool isFallbackDue = !hasSavedCredentials || (!safeMode && offlineMs > FALLBACK_AFTER_MS);
  if (canUseFallback && isFallbackDue) {
    hasTriedFallback = true;
    isOnFallback = true;
    LOG("trying built-in Wi-Fi credentials");
    connectVolatile(WIFI_SSID, WIFI_PASS, 0, nullptr, false);
  }
  if (!hasRestored && offlineMs > RESTORE_AFTER_MS) {
    hasRestored = true;
    if (isOnFallback && hasSavedCredentials) {
      isOnFallback = false;
      LOG("built-in credentials did not connect, back to the saved ones");
      useSavedCredentials();
    }
    if (cfg.phy != 'n') {  // the access point may refuse 11b/g-only clients
      LOG("still offline: allowing 802.11b/g/n");
      WiFi.setPhyMode(WIFI_PHY_MODE_11N);
    }
  }

  // With nothing to connect to there is no point waiting: open the setup AP now.
  bool hasNothingToJoin = !hasSavedCredentials && !hasFallback;
  bool isApDue = hasNothingToJoin || offlineMs > SETUP_AP_AFTER_MS;
  if (!isApUp && isApDue && now - lastApAttempt > SETUP_AP_RETRY_MS) netStartSetupAp();

  // A station hunting for its network makes the setup AP stutter. Go quiet
  // while someone is using it, but never for long: a phone that auto-joins
  // the setup AP must not keep the strip offline.
  bool hasApClient = isApUp && WiFi.softAPgetStationNum() > 0;
  if (!hasApClient) apClientSince = now;
  setReconnectPaused(hasApClient && now - apClientSince < SETUP_PAUSE_MAX_MS);

  if (!isReconnectPaused && now - lastKick > RECONNECT_KICK_MS) {
    lastKick = now;
    LOG("still offline after %lus, %lu failed joins, last: %s", (unsigned long)(offlineMs / 1000),
        (unsigned long)netFailedJoins, discReasonName(netLastReason));
    WiFi.begin();
  }
}

void netBegin() {
  WiFi.persistent(true);  // credentials live in the SDK area, which an OTA keeps
  WiFi.mode(WIFI_STA);
  WiFi.hostname(HOSTNAME);
  WiFi.setSleepMode(WIFI_NONE_SLEEP);
  WiFi.setPhyMode(phyMode(cfg.phy));  // always set: the SDK remembers the last mode in flash
  if (cfg.txDbm > 0) WiFi.setOutputPower(cfg.txDbm);  // 0 = leave the SDK default, as stock did
  WiFi.setAutoReconnect(true);

  onGotIp = WiFi.onStationModeGotIP([](const WiFiEventStationModeGotIP &e) {
    isLinkUp = true;
    LOG("wifi up: AP %s ch%d %ddBm ip %s", WiFi.BSSIDstr().c_str(), (int)WiFi.channel(),
        (int)WiFi.RSSI(), e.ip.toString().c_str());
  });
  onDisconnected = WiFi.onStationModeDisconnected([](const WiFiEventStationModeDisconnected &e) {
    netLastReason = e.reason;
    if (!isLinkUp) { netFailedJoins++; return; }  // a failed retry, not a new drop
    isLinkUp = false;
    netDisconnects++;
    LOG("wifi lost: %s (%d)", discReasonName(e.reason), (int)e.reason);
  });

  hasSavedCredentials = WiFi.SSID().length() > 0;
  if (hasSavedCredentials) WiFi.begin();
  else LOG("no saved Wi-Fi credentials");
  offlineSince = lastRoamCheck = lastKick = millis();
  if (safeMode) netStartSetupAp();
}

void netTick() {
  uint32_t now = millis();
  if (WiFi.status() == WL_CONNECTED) onlineTick(now);
  else offlineTick(now);
}
