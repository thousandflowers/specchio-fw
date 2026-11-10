# specchio-fw

Firmware for the Athom LS5050C(-HK) RGB strip controller on the bedroom mirror.
Replaces Athom's HomeKit firmware v0.8.5.

## Why

Measured on the stock firmware (`docs/baseline-stock.txt`, strip at -88..-91 dBm):

- 27% of pings took 100-200 ms in an alternating pattern: the radio sleeps
  between beacons (modem sleep), so every exchange waits for the next one.
- HTTP requests stalled for 1, 2 and 4 s: lost packets and TCP retransmission.
- ~17 Wi-Fi reconnects in 14 hours.
- The web log prints the Wi-Fi password, without authentication.

## What it does

- **Wi-Fi**: modem sleep off, continuous reconnect, PHY mode and TX power selectable (defaults as stock).
  When the signal is weak it scans every 30 min and moves to a clearly stronger
  access point of the same network (the ESP8266 never roams by itself), so a
  mesh repeater added later is picked up without touching the strip.
- **HomeKit**: on/off, brightness, hue, saturation. Code in `secrets.ini`.
- **Modes**, each also a switch in the Home app: Alba (sunrise that leaves the lamp on),
  Focus (green to red timer with a half-time dip and a red flash at the end), Vibrazione
  (the idle lamp is never perfectly still), Orologio (two blinks at every change of hour).
- **Local API** for scripts, plus a one-page UI at `/` with a live signal reading.
- **Light**: CIE lightness curve (1% is dim, not black), 250 ms fades, state
  restored at power-on before Wi-Fi is up.
- **Cannot-brick measures**, since no serial port is wired:
  - three unrequested reboots in a row (crash, watchdog, out of memory) -> safe mode: no flash storage, no HomeKit;
    lamp, web page and `/update` still work, setup AP is up;
  - 5 min offline -> setup AP `specchio-setup` (password = `OTA_PASS`), http://192.168.4.1;
  - button: short = toggle, 5 s = setup AP (one blink), 15 s = factory reset (three blinks);
  - `/api/log` and `/api/state` expose reset reason, crash address, Wi-Fi drop reasons.

## API

Open on the LAN:

    GET /api/state
    GET /api/log
    GET /api/set?on=1&hex=ff8000&v=60        also h=0-360, s=0-100
    GET /api/raw?r=1023&g=0&b=0              direct PWM duty, to identify channels
    GET /api/effect?name=alba&minutes=20     timed effect: alba (sunrise), focus (timer); name=off stops
    GET /api/flash?hex=ff8000&n=3            coloured blinks over whatever is showing, for events
    GET /api/set?shimmer=30&hourly=1         idle "vibration" 0-100, tick at every change of hour
    GET /api/roam                            scan for a better access point now

Password protected (`admin` / `OTA_PASS`):

    GET  /api/config?pins=12,4,14            GPIOs for R,G,B
    GET  /api/config?phy=g                   b | g | n, applies after reboot
    GET  /api/config?boot=last               last | on | off at power-on
    GET  /api/config?fade=250                ms, 0-5000
    GET  /api/config?tx=0                    max TX power in dBm, 0 = SDK default, applies after reboot
    POST /api/wifi          ssid=..&pass=..
    POST /api/reboot
    POST /api/factory-reset                  HomeKit pairing + settings
    /update                                  firmware upload

## Build, test, flash

    cp secrets.example.ini secrets.ini       # then edit
    pio run
    c++ -std=c++11 -Wall -Isrc test/test_policy.cpp -o /tmp/t && /tmp/t
    c++ -std=c++11 -Wall -Isrc test/test_color.cpp  -o /tmp/t && /tmp/t

    # from this firmware
    curl -u admin:$OTA_PASS -F firmware=@.pio/build/specchio/firmware.bin http://specchio.local/update
    # first time, from the stock Athom firmware (not reversible: Athom does not publish it)
    curl -F file=@.pio/build/specchio/firmware.bin http://<ip>/update

    python3 tools/bench.py specchio.local    # compare with docs/baseline-stock.txt

## Hardware notes

- ESP8266, 2 MB flash (DOUT), same Arduino core (2.7.4) and flash layout as stock.
- PWM on GPIO12, GPIO4, GPIO14; button GPIO0; spare MOSFET on GPIO13 (held low).
  Which GPIO is red or green differs between board revisions: check with
  `/api/raw`, fix with `/api/config?pins=`.
