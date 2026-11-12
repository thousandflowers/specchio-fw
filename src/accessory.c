// HomeKit accessory definition (the library wants it in C).
#include <homekit/homekit.h>
#include <homekit/characteristics.h>

#ifndef HOMEKIT_CODE
#define HOMEKIT_CODE "246-81-357"
#endif

homekit_characteristic_t cha_on = HOMEKIT_CHARACTERISTIC_(ON, true);
homekit_characteristic_t cha_bri = HOMEKIT_CHARACTERISTIC_(BRIGHTNESS, 100);
homekit_characteristic_t cha_hue = HOMEKIT_CHARACTERISTIC_(HUE, 0);
homekit_characteristic_t cha_sat = HOMEKIT_CHARACTERISTIC_(SATURATION, 0);
homekit_characteristic_t cha_name = HOMEKIT_CHARACTERISTIC_(NAME, "specchio");
// Replaced at start-up by the chip's own id (main.cpp): nothing of one device is written in the source.
homekit_characteristic_t cha_serial = HOMEKIT_CHARACTERISTIC_(SERIAL_NUMBER, "000000");

// One switch per mode, so each can be used from the Home app, Siri and scenes.
homekit_characteristic_t cha_fx_alba = HOMEKIT_CHARACTERISTIC_(ON, false);
homekit_characteristic_t cha_fx_focus = HOMEKIT_CHARACTERISTIC_(ON, false);
homekit_characteristic_t cha_shimmer = HOMEKIT_CHARACTERISTIC_(ON, false);
homekit_characteristic_t cha_hourly = HOMEKIT_CHARACTERISTIC_(ON, false);
homekit_characteristic_t cha_claude = HOMEKIT_CHARACTERISTIC_(ON, false);
// Scenes are played by the Mac (tools/scenes.py casa): the strip only holds their switches.
homekit_characteristic_t cha_sc_storm = HOMEKIT_CHARACTERISTIC_(ON, false);
homekit_characteristic_t cha_sc_air = HOMEKIT_CHARACTERISTIC_(ON, false);
homekit_characteristic_t cha_sc_quakes = HOMEKIT_CHARACTERISTIC_(ON, false);
homekit_characteristic_t cha_sc_place = HOMEKIT_CHARACTERISTIC_(ON, false);
homekit_characteristic_t cha_sc_planes = HOMEKIT_CHARACTERISTIC_(ON, false);

// The Home app (iOS 16+) ignores NAME on a service and shows the accessory
// name on every tile; CONFIGURED_NAME is the one it actually displays.
#define MODE_SWITCH(label, characteristic) \
  HOMEKIT_SERVICE(SWITCH, .characteristics = (homekit_characteristic_t *[]){ \
    HOMEKIT_CHARACTERISTIC(NAME, label), HOMEKIT_CHARACTERISTIC(CONFIGURED_NAME, label), \
    characteristic, NULL })

void identify(homekit_value_t _v) {}

homekit_accessory_t *accessories[] = {
  HOMEKIT_ACCESSORY(.id = 1, .category = homekit_accessory_category_lightbulb, .services = (homekit_service_t *[]){
    HOMEKIT_SERVICE(ACCESSORY_INFORMATION, .characteristics = (homekit_characteristic_t *[]){
      &cha_name,
      HOMEKIT_CHARACTERISTIC(MANUFACTURER, "Athom (custom fw)"),
      &cha_serial,
      HOMEKIT_CHARACTERISTIC(MODEL, "LS5050C"),
      HOMEKIT_CHARACTERISTIC(FIRMWARE_REVISION, "2.4.0"),
      HOMEKIT_CHARACTERISTIC(IDENTIFY, identify),
      NULL
    }),
    HOMEKIT_SERVICE(LIGHTBULB, .primary = true, .characteristics = (homekit_characteristic_t *[]){
      &cha_on, &cha_bri, &cha_hue, &cha_sat, NULL
    }),
    MODE_SWITCH("Alba", &cha_fx_alba),
    MODE_SWITCH("Focus", &cha_fx_focus),
    MODE_SWITCH("Vibrazione", &cha_shimmer),
    MODE_SWITCH("Orologio", &cha_hourly),
    MODE_SWITCH("Claude", &cha_claude),
    MODE_SWITCH("Temporale", &cha_sc_storm),
    MODE_SWITCH("Aria", &cha_sc_air),
    MODE_SWITCH("Terremoti", &cha_sc_quakes),
    MODE_SWITCH("Posto lontano", &cha_sc_place),
    MODE_SWITCH("Aerei", &cha_sc_planes),
    NULL
  }),
  NULL
};

homekit_server_config_t hk_config = {
  .accessories = accessories,
  .password = HOMEKIT_CODE,
  // Must go up whenever services are added or removed, or iOS keeps showing
  // the accessory from its cache (stock was 1, v2.0 was 2, v2.2.0 was 3, the
  // Claude switch in 2.6.1 made it 7, the scene switches in 2.7.0 made it 8).
  .config_number = 8,
};
