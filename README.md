# specchio-fw

Custom firmware for the Athom LS5050C(-HK) RGB strip on the bedroom mirror.
Replaces Athom HomeKit fw v0.8.5. Same Arduino core (2.7.4), smaller (536 KB vs 570 KB).

What changed vs stock:
- Wi-Fi modem sleep off (stock lag source), TX 20.5 dBm, 802.11g default, auto-reconnect
- HomeKit kept (code `246-81-357`), plus local HTTP API, 250 ms fades, last state restored at power-on
- Boot-loop guard: 3 quick crashes -> safe mode (no HomeKit, web + `/update` still up)

## API (http://specchio.local or the IP)
- `GET /api/state`
- `GET /api/set?on=1&hex=ff0000&v=80` (also `h`,`s` 0-360/0-100)
- `GET /api/config?pins=12,4,14` (R,G,B GPIOs) · `?phy=b|g|n`
- `POST /api/wifi` (ssid, pass) · `POST /api/homekit-reset` · `POST /api/reboot`
- `/update` firmware upload, user `admin` pass `specchio`

## Recovery
No Wi-Fi for 90 s -> AP `specchio-setup` (pass `specchio1`), open http://192.168.4.1, set Wi-Fi.

## Build / flash
    pio run
    curl -u admin:specchio -F firmware=@.pio/build/specchio/firmware.bin http://specchio.local/update
First flash from stock: `curl -F file=@.pio/build/specchio/firmware.bin http://<ip>/update`

Test colour logic: `c++ -std=c++17 -Isrc test/test_color.cpp -o /tmp/t && /tmp/t`
