// Experiment builds only (-DSELF_RESTORE -DGOOD_FW_MD5=...).
//
// The strip has no serial port, and a firmware that cannot join the network
// needs a person to recover it through the setup AP. With this guard a
// known-good image is kept at the top of the free flash (the "stash") and the
// firmware asks the bootloader to install it when it has not joined within a
// minute of boot. The bootloader copies it exactly as it copies an OTA.
//
// Rule for this file: it must not add RAM data. The bug being hunted follows
// the binary's layout, so the guard keeps its state in RTC memory and its text
// in flash; a guarded build has the same RAM map as the build it guards.
// No string literals outside PSTR/F, no statics, no LOG.
//
// Flash map (2 MB, file system at 0x1E0000):
//   sketch            [0x000000, 0x098000)   a guarded sketch must stay below 0x98000
//   web OTA staging   [sketch end + 0x1000, + new image)   so at most up to 0x131000
//   stash header      [0x153000, 0x154000)
//   stash image       [0x154000, 0x1E0000)
//
// RTC user memory: blocks 0-31 are the bootloader's command (eboot_command).
// The boot-loop guard in main.cpp writes blocks 0-2, so restartClean() after
// arming the bootloader erases the command: here the reboot is a plain
// ESP.restart(), and the guard's own state lives at block 40.
#ifdef SELF_RESTORE
#include <ESP8266WebServer.h>
#include <MD5Builder.h>
#include "app.h"
extern "C" {
#include <eboot_command.h>
}

#define SECTOR 4096u
#define STASH_HEADER_AT 0x153000u
#define STASH_DATA_AT 0x154000u
#define STASH_MAX 0x8C000u
#define STASH_MAGIC 0x57A5E002u
#define GUARD_MAGIC 0x60A2D003u
#define GUARD_RTC_BLOCK 40u
#define GUARD_GRACE_MS 60000u     // good builds join 3 s after boot
#define GUARD_MAX_TRIES 2u        // then give up: the setup AP still opens at 5 minutes
#define COPY_CHUNK 1024u
#define STAGE_DIED_INSIDE 99u     // still there after a reboot = the attempt never returned

struct StashHeader { uint32_t magic, size; char md5[36]; };
// stage: where the last restore attempt stopped, 0 = bootloader armed.
struct GuardRtc { uint32_t magic, tries, hasJoined, stage; };

static GuardRtc rtcRead() {
  GuardRtc r;
  bool isOurs = ESP.rtcUserMemoryRead(GUARD_RTC_BLOCK, (uint32_t *)&r, sizeof r) && r.magic == GUARD_MAGIC;
  if (!isOurs) { r.magic = GUARD_MAGIC; r.tries = 0; r.hasJoined = 0; r.stage = 0; }
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

// What the stash holds and how the last restore attempt went.
void guardDescribe(char *out, size_t len) {
  StashHeader h;
  char actual[33];
  actual[0] = '\0';
  bool isValid = stashHeader(h);
  if (isValid) stashMd5(h.size, actual);
  GuardRtc r = rtcRead();
  snprintf_P(out, len, PSTR("stash=%d size=%u actual_md5=%s is_known_good=%d tries=%u joined=%u last_stage=%u\n"),
             (int)isValid, (unsigned)(isValid ? h.size : 0), actual, (int)(strcmp_P(actual, PSTR(GOOD_FW_MD5)) == 0),
             (unsigned)r.tries, (unsigned)r.hasJoined, (unsigned)r.stage);
}

// Arm the bootloader to install the stash at the next reboot. Returns 0 when
// armed: the caller must then ESP.restart() without touching RTC memory.
// Otherwise the step that refused. Only the image whose md5 was compiled in is
// ever installed: a half-written or wrong stash cannot become the firmware.
int guardRestore() {
  GuardRtc r = rtcRead();
  r.stage = STAGE_DIED_INSIDE;
  rtcWrite(r);

  StashHeader h;
  char actual[33];
  if (!stashHeader(h)) r.stage = 1;
  else if (!stashMd5(h.size, actual)) r.stage = 2;
  else if (strcmp_P(actual, PSTR(GOOD_FW_MD5)) != 0) r.stage = 3;
  else r.stage = 0;
  rtcWrite(r);
  if (r.stage != 0) return r.stage;

  eboot_command install;
  install.action = ACTION_COPY_RAW;
  install.args[0] = STASH_DATA_AT;  // from
  install.args[1] = 0;              // to: the sketch
  install.args[2] = h.size;
  eboot_command_write(&install);
  return 0;
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
  if (guardRestore() == 0) ESP.restart();
}
#endif
