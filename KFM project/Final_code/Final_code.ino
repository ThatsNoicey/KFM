#include <Arduino_LSM6DSOX.h>
#include <Wire.h>
#include <Adafruit_DRV2605.h>
#include <ArduinoBLE.h>

// ------------------------- BLE Setup -------------------------
BLEService kneeService("12345678-1234-5678-1234-56789abcdef0");
BLEFloatCharacteristic kneeAngleCharacteristic("12345678-1234-5678-1234-56789abcdef1",
                                              BLERead | BLENotify);
BLEUnsignedLongCharacteristic holdTimeCharacteristic("12345678-1234-5678-1234-56789abcdef2",
                                                    BLERead | BLENotify);
BLEFloatCharacteristic peakAngleCharacteristic("12345678-1234-5678-1234-56789abcdef3",
                                              BLERead | BLENotify);

// ------------------------- DRV2605 (Haptics) -------------------------
Adafruit_DRV2605 drv;
#define LED_PIN 13

// ------------------------- Kalman Filter Variables -------------------------
float angle = 0.0f; // Kalman-filtered angle
float bias = 0.0f;
float P[2][2] = { { 1, 0 }, { 0, 1 } };
float Q_angle = 0.01f;
float Q_bias  = 0.005f;
float R_measure = 0.02f;

// ------------------------- Fallback Gyro Integration -------------------------
float fallbackAngle = 0.0f; // Used if Kalman fails or sensor data is bad
float lastGoodGz = 0.0f;    // Store last valid gyroscope z-axis value
float Thresh = 30.0f; // angle pass threshold

// ------------------------- Timing & Tracking -------------------------
unsigned long lastTime;
unsigned long belowThresholdStartTime = 0;
unsigned long currentHoldDuration     = 0;
float peakAngle                       = 0.0f;

// ------------------------- State Flags -------------------------
bool hasCrossedThreshold = false;
bool isBelowThreshold    = false;
bool imuFunctional       = false;
bool drvFunctional       = false;
bool bleConnected        = false;
bool imuErrorFlag        = false; // Flag for IMU sensor errors
bool vibrationActive     = false;

// ------------------------- Knee Angle Variables -------------------------
float angleKnee          = 0.0f; // Final angle for BLE
float holdTime           = 0.0f; // Hold time in seconds

// ------------------------- Setup -------------------------
void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);

  // Initialize IMU, BLE, DRV
  bool imuInitialized = IMU.begin();
  bool bleInitialized = BLE.begin();
  bool drvInitialized = drv.begin(); // comment/uncomment if needed

  if (!imuInitialized) {
    Serial.println("IMU sensor not detected!");
    while (1);
  }
  if (!bleInitialized) {
    Serial.println("Failed to initialize BLE!");
    while (1);
  }
  if (!drvInitialized) {
    Serial.println("Could not find DRV2605 (haptics disabled).");
  }

  // Set up BLE
  BLE.setLocalName("NanoKneeTracker");
  BLE.setAdvertisedService(kneeService);
  kneeService.addCharacteristic(kneeAngleCharacteristic);
  kneeService.addCharacteristic(holdTimeCharacteristic);
  kneeService.addCharacteristic(peakAngleCharacteristic);
  BLE.addService(kneeService);
  BLE.advertise();

  // Set up DRV2605 if available
  if (drvInitialized) {
    drv.selectLibrary(1);              // ERM motor effects library
    drv.setMode(DRV2605_MODE_INTTRIG);   // internal trigger
    drvFunctional = true;
  }

  lastTime = millis();
}

// ------------------------- Check IMU Functionality -------------------------
void checkIMUFunctionality() {
  imuFunctional = IMU.gyroscopeAvailable() && IMU.accelerationAvailable();
}

// ------------------------- Update Peak Angle -------------------------
void updatePeakAngle() {
  if (angleKnee > peakAngle) {
    peakAngle = angleKnee;
  }
  // Reset peak if angle goes back below threshold
  if (angleKnee <= Thresh) {
    peakAngle = 0.0f;
  }
}

// ------------------------- loop() -------------------------
void loop() {
  BLE.poll();
  bleConnected = BLE.connected();

  checkIMUFunctionality();

  unsigned long currentTime = millis();
  float dt = (currentTime - lastTime) / 1000.0f;
  lastTime = currentTime;
  if (dt <= 0.0f) dt = 0.01f;

  if (imuFunctional && !imuErrorFlag) {
    float gx, gy, gz;
    float ax, ay, az;
    IMU.readGyroscope(gx, gy, gz);
    IMU.readAcceleration(ax, ay, az);

    // Validate sensor readings
    bool sensorError = false;
    if (isnan(ax)) { Serial.println("Error: ax is NaN"); sensorError = true; }
    if (isnan(ay)) { Serial.println("Error: ay is NaN"); sensorError = true; }
    if (isnan(az)) { Serial.println("Error: az is NaN"); sensorError = true; }
    if (isnan(gx)) { Serial.println("Error: gx is NaN"); sensorError = true; }
    if (isnan(gy)) { Serial.println("Error: gy is NaN"); sensorError = true; }
    if (isnan(gz)) { Serial.println("Error: gz is NaN"); sensorError = true; }

    if (sensorError) {
      Serial.println("IMU sensor error detected. Switching to fallback integration.");
      imuErrorFlag = true; // disable further IMU usage
    } else {
      // Save valid gyroscope reading
      lastGoodGz = gz;

      // ------------------------- Compute Accelerometer Angle -------------------------
      // Define "flat" as 0° and "vertical" as 90°.
      float denominator = sqrt(ax * ax + ay * ay);
      if (denominator < 1e-6) denominator = 1e-6;
      float rawAngle   = atan2(az, denominator) * 180.0f / PI;
      float accelAngle = 90.0f - rawAngle; // Adjust so that flat = 0°, vertical = 90°

      // ------------------------- Kalman Filter Predict -------------------------
      float gyroRate = gz - bias;
      float anglePrev = angle;
      angle += gyroRate * dt;
      P[0][0] += dt * (dt * P[1][1] - P[0][1] - P[1][0] + Q_angle);
      P[0][1] -= dt * P[1][1];
      P[1][0] -= dt * P[1][1];
      P[1][1] += Q_bias * dt;

      // ------------------------- Kalman Filter Update -------------------------
      float S = P[0][0] + R_measure;
      float K[2];
      K[0] = P[0][0] / S;
      K[1] = P[1][0] / S;
      float y = accelAngle - angle;
      angle  += K[0] * y;
      bias   += K[1] * y;
      float P00_temp = P[0][0];
      float P01_temp = P[0][1];
      P[0][0] -= K[0] * P[0][0];
      P[0][1] -= K[0] * P[0][1];
      P[1][0] -= K[1] * P00_temp;
      P[1][1] -= K[1] * P01_temp;

      // Check for NaN or Inf after Kalman update
      if (isnan(angle) || isinf(angle)) {
        Serial.println("Kalman filter produced an invalid angle. Reverting to fallback integration.");
        angle = anglePrev + gz * dt;
      }

      // Keep fallbackAngle in sync if things are OK
      fallbackAngle = angle;

      // ------------------------- Normalize Angle -------------------------
      if (angle > 180.0f)  angle -= 360.0f;
      if (angle < -180.0f) angle += 360.0f;

      angleKnee = angle;
    }
  } else {
    // Fallback integration when IMU is not used or error flagged.
    float gx, gy, gz;
    if (IMU.gyroscopeAvailable()) {
      IMU.readGyroscope(gx, gy, gz);
      // If current reading is invalid, use lastGoodGz
      if (!isnan(gz)) {
        lastGoodGz = gz;
        fallbackAngle += gz * dt;
      } else {
        fallbackAngle += lastGoodGz * dt;
      }
    } else {
      fallbackAngle += lastGoodGz * dt;
    }
    angleKnee = fallbackAngle;
  }

  // ------------------------- Update Peak & Threshold Logic -------------------------
  updatePeakAngle();
  // Now measure hold time when angle is over 50°
  if (angleKnee > Thresh) {
    if (!isBelowThreshold) {
      isBelowThreshold = true;
      belowThresholdStartTime = millis();
    }
    currentHoldDuration = max(15, millis() - belowThresholdStartTime);
  } else {
    isBelowThreshold = false;
    currentHoldDuration = 0;
  }
  // ------------------------- Write to BLE -------------------------
  kneeAngleCharacteristic.writeValue(angleKnee);
  holdTimeCharacteristic.writeValue(currentHoldDuration);
  peakAngleCharacteristic.writeValue(peakAngle);

  // ------------------------- Haptic & LED Feedback -------------------------
  if (angleKnee > Thresh && !hasCrossedThreshold) {
    hasCrossedThreshold = true;
    if (drvFunctional) {
      for (int i = 0; i < 4; i++) {
        drv.setWaveform(i, 19);
      }
      drv.setWaveform(4, 0);
      drv.go();
    }
    digitalWrite(LED_PIN, HIGH);
    delay(200);
    digitalWrite(LED_PIN, LOW);
  } else if (angleKnee <= Thresh) {
    if (hasCrossedThreshold) {
      if (drvFunctional) {
        for (int i = 0; i < 2; i++) {
          drv.setWaveform(i, 19);
        }
        drv.setWaveform(2, 0);
        drv.go();
      }
      digitalWrite(LED_PIN, HIGH);
      delay(200);
      digitalWrite(LED_PIN, LOW);
    }
    hasCrossedThreshold = false;
  }
    //--check for overexertion
    if (angleKnee > 135) {
        if (!vibrationActive) {
            Serial.println("Vibration ON");  // Debugging
            vibrationActive = true;
        }

        // Continuously trigger effect 47 (strong buzz)
        drv.setWaveform(0, 47);  // Strong buzz effect
        drv.setWaveform(1, 0);   // End waveform
        drv.go();  // Start effect
    } 
    
    else if (vibrationActive) {
        Serial.println("Vibration OFF");  // Debugging
        drv.stop();  // Stop haptic feedback
        vibrationActive = false;
    }
  

  delay(10);
  holdTime = currentHoldDuration / 1000.0f;

  // ------------------------- Serial Output -------------------------
  Serial.print("ANGLE: ");
  Serial.print(angleKnee, 1);
  Serial.print("°\tHOLD: ");
  Serial.print(holdTime, 2);
  Serial.print("s\tPEAK: ");
  Serial.print(peakAngle, 1);
  Serial.print("°\tSTATUS: ");
  Serial.print(bleConnected ? 'B' : 'b');
  Serial.print(imuFunctional ? 'I' : 'i');
  Serial.println(drvFunctional ? 'D' : 'd');
}
