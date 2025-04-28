#include <Arduino_LSM6DSOX.h>
#include <Adafruit_DRV2605.h>

Adafruit_DRV2605 drv;

void setup() {
  // put your setup code here, to run once:
drv.selectLibrary(1);
drv.setMode(DRV2605_MODE_INTTRIG);
}

void loop() {
  // put your main code here, to run repeatedly:
drv.setWaveform(0,14); //Strong buzz
drv.setWaveform(1,19); //Strong Click - 60%
drv.setWaveform(2,37); //Long Double Sharp Click Strong
drv.setWaveform(3,47); //Buzz 1
drv.setWaveform(4,52); //Pulsing Strong 1
drv.setWaveform(5,118); //Long Buzz
drv.go();
delay(2000);
}
