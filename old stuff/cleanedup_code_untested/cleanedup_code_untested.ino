#include <Arduino_LSM6DSOX.h>
#include <Wire.h>
#include <Adafruit_DRV2605.h>
#include <ArduinoBLE.h>

// Hardware Constants
#define LED_PIN 13              // Built-in LED pin
#define BLE_SERVICE_UUID "12345678-1234-5678-1234-56789abcdef0"
#define BLE_CHARACTERISTIC_UUID "12345678-1234-5678-1234-56789abcdef1"
#define VIBRATION_EFFECT 19     // DRV2605 strong buzz effect
#define ANGLE_THRESHOLD 50      // Trigger angle in degrees
#define KALMAN_DT 0.01f         // Fixed timestep for Kalman filter

// Global Objects
Adafruit_DRV2605 drv;           // Haptic driver
BLEService kneeService(BLE_SERVICE_UUID);
BLEFloatCharacteristic kneeAngleChar(BLE_CHARACTERISTIC_UUID, BLERead | BLENotify);

// Kalman Filter Variables
float angle = 0;                // Estimated angle
float bias = 0;                 // Gyro bias
float P[2][2] = {{1, 0}, {0, 1}}; // Error covariance matrix
const float Q_angle = 0.01;     // Process noise variance
const float Q_bias = 0.005;     // Bias process noise
const float R_measure = 0.02;   // Measurement noise variance

// System State
bool hasCrossedThreshold = false; // Angle threshold tracking

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);

  // Initialize IMU
  if (!IMU.begin()) {
    logError("IMU initialization failed");
    while(1);
  }

  // Initialize BLE
  if (!BLE.begin()) {
    logError("BLE initialization failed");
    while(1);
  }
  setupBLE();

  // Initialize Haptic Driver
  if (!drv.begin()) {
    logError("DRV2605 initialization failed");
    while(1);
  }
  configureHaptics();
}

void loop() {
  static unsigned long lastUpdate = millis();
  
  // Process sensor data at fixed intervals
  if (millis() - lastUpdate >= 10) {
    lastUpdate = millis();
    processSensorData();
  }

  // Handle BLE connections
  BLE.poll();
}

//region Sensor Processing
void processSensorData() {
  float gx, gy, gz, ax, ay, az;
  
  if (IMU.gyroscopeAvailable() && IMU.accelerationAvailable()) {
    IMU.readGyroscope(gx, gy, gz);
    IMU.readAcceleration(ax, ay, az);

    // Calculate angles
    float accelAngle = calculateAccelAngle(ax, ay, az);
    float gyroRate = gz - bias;
    
    // Kalman prediction
    angle += KALMAN_DT * gyroRate;
    updateCovarianceMatrix();

    // Kalman update
    float y = accelAngle - angle;
    float S = P[0][0] + R_measure;
    float K[] = {P[0][0]/S, P[1][0]/S};
    angle += K[0] * y;
    bias += K[1] * y;

    // Normalize angle
    float angleKnee = normalizeAngle(angle);
    
    // Update BLE characteristic
    kneeAngleChar.writeValue(angleKnee);
    
    // Handle threshold crossing
    handleThreshold(angleKnee);
  }
}

float calculateAccelAngle(float ax, float ay, float az) {
  float denominator = sqrt(ax*ax + ay*ay);
  denominator = fmax(denominator, 0.01);  // Prevent division by zero
  return atan2(az, denominator) * 180.0/PI;
}

float normalizeAngle(float rawAngle) {
  // Keep angle within -180 to +180 degrees
  return fmod(rawAngle + 180.0, 360.0) - 180.0;
}

void updateCovarianceMatrix() {
  // Kalman filter covariance prediction
  P[0][0] += KALMAN_DT * (KALMAN_DT*P[1][1] - P[0][1] - P[1][0] + Q_angle);
  P[0][1] -= KALMAN_DT * P[1][1];
  P[1][0] = P[0][1];
  P[1][1] += Q_bias * KALMAN_DT;

  // Maintain minimum covariance values
  P[0][0] = fmax(P[0][0], 0.1);
  P[1][1] = fmax(P[1][1], 0.1);
}
//endregion

//region Threshold Handling
void handleThreshold(float currentAngle) {
  if (currentAngle > ANGLE_THRESHOLD && !hasCrossedThreshold) {
    hasCrossedThreshold = true;
    triggerFeedback();
  } 
  else if (currentAngle <= ANGLE_THRESHOLD) {
    hasCrossedThreshold = false;
  }
}

void triggerFeedback() {
  // Visual feedback
  digitalWrite(LED_PIN, HIGH);
  delay(200);
  digitalWrite(LED_PIN, LOW);

  // Haptic feedback
  drv.setWaveform(0, VIBRATION_EFFECT);  // Slot 0
  drv.setWaveform(1, VIBRATION_EFFECT);  // Slot 1
  drv.setWaveform(2, VIBRATION_EFFECT);  // Slot 2
  drv.setWaveform(3, 0);                 // End sequence
  drv.go();
}
//endregion

//region System Configuration
void setupBLE() {
  BLE.setLocalName("NanoKneeTracker");
  BLE.setAdvertisedService(kneeService);
  kneeService.addCharacteristic(kneeAngleChar);
  BLE.addService(kneeService);
  BLE.advertise();
}

void configureHaptics() {
  drv.selectLibrary(1);                 // 1 = ERM, 6 = LRA
  drv.setMode(DRV2605_MODE_INTTRIG);    // Internal trigger mode
  drv.useLRA();                         // Uncomment if using LRA motor
}
//endregion

//region Error Handling
void logError(const char* message) {
  Serial.print("ERROR: ");
  Serial.println(message);
  
  // Simple LED error indicator
  for(int i=0; i<3; i++) {
    digitalWrite(LED_PIN, HIGH);
    delay(100);
    digitalWrite(LED_PIN, LOW);
    delay(100);
  }
}
//endregion
