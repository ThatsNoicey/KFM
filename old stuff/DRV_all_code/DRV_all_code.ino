#include <Arduino_LSM6DSOX.h>
#include <Wire.h>
#include <Adafruit_DRV2605.h>
#include <ArduinoBLE.h>

// BLE Initialisation
BLEService kneeService("12345678-1234-5678-1234-56789abcdef0");
BLEFloatCharacteristic kneeAngleCharacteristic("12345678-1234-5678-1234-56789abcdef1", BLERead | BLENotify);

//BLE Initialisation
Adafruit_DRV2605 drv;
#define LED_PIN 13

// Kalman Filter Variables
float angle = 0;
float bias = 0;
float P[2][2] = {{1, 0}, {0, 1}};
float Q_angle = 0.01;
float Q_bias = 0.005;
float R_measure = 0.02;
float dtt = 0.01;
unsigned long lastTime;

// Data Storage
float peakValues[20];
byte arrayIndex = 0;
bool stopFlag = false;
bool hasCrossedThreshold = false; // Flag to track if the threshold was crossed

// Knee Angle Variables
float angleKnee = 0;
float threshold = 90;
bool aboveThreshold = false;
float peakValue = 0;

void setup() {
  Serial.begin(115200);

  if (!IMU.begin()) {
    Serial.println("IMU sensor not detected!");
    while (1);
  }

  if (!BLE.begin()) {
    Serial.println("Failed to initialize BLE!");
    while (1);
  }

  if (!drv.begin()) {
    Serial.println("Could not find DRV2605");
    while (1); 
  }
  Serial.println("DRV2605 initialized.");

  BLE.setLocalName("NanoKneeTracker");
  BLE.setAdvertisedService(kneeService);
  kneeService.addCharacteristic(kneeAngleCharacteristic);
  BLE.addService(kneeService);
  BLE.advertise();
  
  lastTime = millis();

  drv.selectLibrary(1); // 1 = ERM, 6 = LRA (Linear Resonant Actuator)
  drv.setMode(DRV2605_MODE_INTTRIG); // Internal trigger mode
}

void loop() {
  // [Previous STOP command logic unchanged]
  
  float gx, gy, gz;
  float ax, ay, az;
  
  if (IMU.gyroscopeAvailable() && IMU.accelerationAvailable()) {
    IMU.readGyroscope(gx, gy, gz);
    IMU.readAcceleration(ax, ay, az);

    // FIXED ACCEL ANGLE CALCULATION
    float denominator = sqrt(ax*ax + ay*ay);
    denominator = (denominator < 0.01 && denominator > -0.01) ? 0.01 : denominator; // Gentle clamp
    float accelAngle = atan2(az, denominator) * 180.0/PI;

    // RESTORE ORIGINAL KALMAN TIMESTEP
    float dt = 0.01; // Force fixed timestep instead of millis() calculation
    float gyroRate = gz - bias;

    // Kalman predict
    angle += dt * gyroRate;
    P[0][0] += dt * (dt*P[1][1] - P[0][1] - P[1][0] + Q_angle);
    P[0][1] -= dt * P[1][1];
    P[1][0] -= dt * P[1][1];
    P[1][1] += Q_bias * dt;

    // LESS AGGRESSIVE COVARIANCE PROTECTION
    if(P[0][0] < 0.1) P[0][0] = 0.1;  // Prevent collapse
    if(P[1][1] < 0.1) P[1][1] = 0.1;

    // Kalman update
    float S = P[0][0] + R_measure;
    float K[2] = {P[0][0]/S, P[1][0]/S};  // No artificial S clamping
    
    float y = accelAngle - angle;
    angle += K[0] * y;
    bias += K[1] * y;

    // ORIGINAL ANGLE NORMALIZATION
    angleKnee = angle;
    if(angleKnee > 180) angleKnee -= 360;
    else if(angleKnee < -180) angleKnee += 360;
    
    Serial.print("RAW ANGLE: ");
    Serial.print(angle);
    Serial.print(" | NORMALIZED: ");
    Serial.println(angleKnee);
    if (angleKnee > 50 && !hasCrossedThreshold) {
      hasCrossedThreshold = true; // Set flag to prevent retriggering
      drv.setWaveform(0, 19); // Effect 19: strong buzz
      drv.setWaveform(0, 19);
      drv.setWaveform(0, 19);
      drv.setWaveform(0, 19);
      drv.setWaveform(1, 0); // End waveform
      drv.go();
    digitalWrite(LED_PIN, HIGH); // Turn LED on
    delay(200); // Keep LED on for 200ms
    digitalWrite(LED_PIN, LOW); // Turn LED off
    } else if (angleKnee <= 50) {
      hasCrossedThreshold = false; // Reset flag when angle falls below 50 degrees
  }
  }

  delay(10);
}