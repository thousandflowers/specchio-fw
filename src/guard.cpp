// Experiment builds only (-DSELF_RESTORE).
//
// The strip has no serial port, and a firmware that cannot join the network
// needs a person to recover it through the setup AP. With this guard a
// known-good image is kept in a spare flash region (the "stash") and written
// back by the firmware itself when it has not joined within a minute of boot.
// The restore goes through the core's Updater, the same path as every OTA.
//
// Rule for this file: it must not add RAM data. The bug being hunted follows
// the binary's layout, so the guard keeps its state in RTC memory and its text
// in flash; a guarded build has the same RAM map as the build it guards.
// No string literals outside PSTR/F, no statics, no LOG.
//
// Flash map (2 MB, file system at 0x1E0000):
//   sketch            [0x000000, 0x098000)   so a guarded sketch must stay below 0x98000
//   stash header      [0x098000, 0x099000)
//   stash image       [0x099000, 0x125000)
//   OTA staging       [0x148000, 0x1E0000)   at most, for a sketch of 0x98000
#ifdef SELF_RESTORE
#include <ESP8266WebServer.h>
#include <MD5Builder.h>
#include <Updater.h>
#include "app.h"

#define SECTOR 4096u
#define STASH_HEADER_AT 0x98000u
#define STASH_DATA_AT 0x99000u
#define STASH_MAX 0x8C000u
#define STASH_MAGIC 0x57A5E001u
#define GUARD_MAGIC 0x60A2D001u
#define GUARD_RTC_BLOCK 8u        // the boot-loop guard owns blocks 0-2
#define GUARD_GRACE_MS 60000u     // good builds join 3 s after boot
#define GUARD_MAX_TRIES 2u        // then give up: the setup AP still opens at 5 minutes
#define COPY_CHUNK 1024u

struct StashHeader { uint32_t magic, size; char md5[36]; };
struct GuardRtc { uint32_t magic, tries, hasJoined; };

static GuardRtc rtcRead() {
  GuardRtc r;
  bool isOurs = ESP.rtcUserMemoryRead(GUARD_RTC_BLOCK, (uint32_t *)&r, sizeof r) && r.magic == GUARD_MAGIC;
  if (!isOurs) { r.magic = GUARD_MAGIC; r.tries = 0; r.hasJoined = 0; }
  return r;
}

static void rtcWrite(GuardRtc r) { ESP.rtcUserMemoryWrite(GUARD_RTC_BLOCK, (uint32_t *)&r, sizeof r); }

static bool stashHeader(StashHeader &h) {
  if (!ESP.flashRead(STASH_HEADER_AT, (uint32_t *)&h, sizeof h)) return false;
  h.md5[32] = '\0';
  return h.magic == STASH_MAGIC && h.size > 0 && h.size <= STASH_MAX;
}

// MD5 of what is really in the stash, as 32 hex characters.
static bool stashMd5(uint32_t size, char *out) {
  uint32_t *buf = (uint32_t *)malloc(COPY_CHUNK);
  if (!buf) return false;
  MD5Builder md5;
  md5.begin();
  bool ok = true;
  for (uint32_t at = 0; ok && at < size; at += COPY_CHUNK) {
    uint32_t n = size - at < COPY_CHUNK ? size - at : COPY_CHUNK;
    ok = ESP.flashRead(STASH_DATA_AT + at, buf, (n + 3) & ~3u);
    md5.add((uint8_t *)buf, n);
    ESP.wdtFeed();
  }
  free(buf);
  md5.calculate();
  md5.getChars(out);
  return ok;
}

// One chunk of an upload to /api/stash. Chunks arrive in order and are 2048
// bytes except the last, so every write starts on a 4-byte boundary.
void guardStashChunk(HTTPUpload &up) {
  if (up.status == UPLOAD_FILE_START) {
    ESP.flashEraseSector(STASH_HEADER_AT / SECTOR);  // no header = no stash while it is half written
    return;
  }
  if (up.status == UPLOAD_FILE_WRITE) {
    uint32_t at = up.totalSize, n = up.currentSize;  // totalSize does not count this chunk yet
    if (n == 0 || at + n > STASH_MAX) return;
    uint32_t *aligned = (uint32_t *)malloc(HTTP_UPLOAD_BUFLEN);
    if (!aligned) return;
    memset(aligned, 0xFF, HTTP_UPLOAD_BUFLEN);
    memcpy(aligned, up.buf, n);
    for (uint32_t start = (at + SECTOR - 1) & ~(SECTOR - 1); start < at + n; start += SECTOR) {
      ESP.flashEraseSector((STASH_DATA_AT + start) / SECTOR);
    }
    ESP.flashWrite(STASH_DATA_AT + at, aligned, (n + 3) & ~3u);
    free(aligned);
    return;
  }
  if (up.status == UPLOAD_FILE_END && up.totalSize > 0 && up.totalSize <= STASH_MAX) {
    StashHeader h;
    memset(&h, 0, sizeof h);
    h.magic = STASH_MAGIC;
    h.size = up.totalSize;
    if (stashMd5(h.size, h.md5)) ESP.flashWrite(STASH_HEADER_AT, (uint32_t *)&h, sizeof h);
  }
}

// What the stash holds, for a human to compare with the md5 of the uploaded file.
void guardDescribe(char *out, size_t len) {
  StashHeader h;
  char actual[33];
  actual[0] = '\0';
  bool isValid = stashHeader(h);
  if (isValid) stashMd5(h.size, actual);
  GuardRtc r = rtcRead();
  snprintf_P(out, len, PSTR("stash=%d size=%u stored_md5=%s actual_md5=%s tries=%u joined=%u\n"),
             (int)isValid, (unsigned)(isValid ? h.size : 0), isValid ? h.md5 : actual, actual,
             (unsigned)r.tries, (unsigned)r.hasJoined);
}

// Stage the stashed image as an update. True = reboot now and the bootloader installs it.
bool guardRestore() {
  StashHeader h;
  char actual[33];
  if (!stashHeader(h) || !stashMd5(h.size, actual) || strcmp(actual, h.md5) != 0) return false;
  uint32_t *buf = (uint32_t *)malloc(COPY_CHUNK);
  if (!buf) return false;
  bool ok = Update.begin(h.size, U_FLASH) && Update.setMD5(h.md5);
  for (uint32_t at = 0; ok && at < h.size; at += COPY_CHUNK) {
    uint32_t n = h.size - at < COPY_CHUNK ? h.size - at : COPY_CHUNK;
    ok = ESP.flashRead(STASH_DATA_AT + at, buf, (n + 3) & ~3u) && Update.write((uint8_t *)buf, n) == n;
    ESP.wdtFeed();
    yield();
  }
  free(buf);
  return ok && Update.end();  // end() checks the staged copy against the md5 before arming the bootloader
}

void guardBoot() {
  GuardRtc r = rtcRead();
  r.hasJoined = 0;
  rtcWrite(r);
}

void guardTick() {
  bool isUp = WiFi.status() == WL_CONNECTED;
  if (!isUp && millis() < GUARD_GRACE_MS) return;
  GuardRtc r = rtcRead();
  if (isUp) {
    if (!r.hasJoined || r.tries) { r.hasJoined = 1; r.tries = 0; rtcWrite(r); }
    return;
  }
  // A link lost after a good join is ordinary life at this signal, not a bad firmware.
  if (r.hasJoined || r.tries >= GUARD_MAX_TRIES) return;
  r.tries++;
  rtcWrite(r);
  if (guardRestore()) restartClean();
}
#endif
