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

void identify(homekit_value_t _v) {}

homekit_accessory_t *accessories[] = {
  HOMEKIT_ACCESSORY(.id = 1, .category = homekit_accessory_category_lightbulb, .services = (homekit_service_t *[]){
    HOMEKIT_SERVICE(ACCESSORY_INFORMATION, .characteristics = (homekit_characteristic_t *[]){
      &cha_name,
      HOMEKIT_CHARACTERISTIC(MANUFACTURER, "Athom (custom fw)"),
      HOMEKIT_CHARACTERISTIC(SERIAL_NUMBER, "000000"),
      HOMEKIT_CHARACTERISTIC(MODEL, "LS5050C"),
      HOMEKIT_CHARACTERISTIC(FIRMWARE_REVISION, "1.0.0"),
      HOMEKIT_CHARACTERISTIC(IDENTIFY, identify),
      NULL
    }),
    HOMEKIT_SERVICE(LIGHTBULB, .primary = true, .characteristics = (homekit_characteristic_t *[]){
      &cha_on, &cha_bri, &cha_hue, &cha_sat, NULL
    }),
    NULL
  }),
  NULL
};

homekit_server_config_t hk_config = {
  .accessories = accessories,
  .password = HOMEKIT_CODE,
};
