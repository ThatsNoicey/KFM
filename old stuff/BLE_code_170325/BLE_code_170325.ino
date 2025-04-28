#include <Arduino_LSM6DSOX.h>
#include <Wire.h>
#include <Adafruit_DRV2605.h>
#include <ArduinoBLE.h>

//ble initialisation 
BLEService kneeService("12345678-1234-5678-1234-56789abcdef0"); // Custom Service
BLEFloatCharacteristic kneeAngleCharacteristic("12345678-1234-5678-1234-56789abcdef1", 
                                               BLERead | BLENotify);
//kalman variables
float angle = 0;
float bias = 0;
float P[2][2]={{1, 0}, {0, 1}};
float Q_angle = 0.01;
float Q_bias = 0.005;
float R_measure = 0.02;
float dtt = 0.01;
unsigned long lastTime;

Adafruit_DRV2605 drv; // introduce the buzzer

//define the array used to store the data
int anArray[20];
byte arrayIndex = 0;
bool stopFlag = false;

// function that prints the array
void printArray() {
  
  Serial.println("stored values: ");
  for (int i = 0; i < arrayIndex; i++) {
    Serial.println(anArray[i]);
  } 
  Serial.println("program stopped"); 
}
// define knee angle variables
float angleKnee = 0;  // Stores computed knee angle
float threshold = 90; // define threshold for peak detection
bool aboveThreshold = false; // bool to track crossing of threshold
float peakValue = 0; // store highest value


void setup() {
  Serial.begin(115200);
  while (!Serial);  // Wait for Serial Monitor
//  if (!drv.begin()){
//    Serial.println("could not find DRV2605");
//    while (1);
//  }
//  drv.selectLibrary(1); // use ERM motor effects
//  drv.setMode(DRV2605_MODE_INTTRIG);

// check everythings working
  if (stopFlag) return;
 
  if (!IMU.begin()) {
    Serial.println("IMU sensor not detected!");
    while (1);
  }
  Serial.println("IMU initialized.");
  if (!BLE.begin()){
    Serial.println("failed to init BLE");
    while (1);
    }
 
  lastTime = millis();  
  BLE.setLocalName("NanoKneeTracker");
  BLE.setAdvertisedService(kneeService);
  kneeService.addCharacteristic(kneeAngleCharacteristic);
  BLE.addService(kneeService);

  BLE.setLocalName("JesusIsLord");
  BLE.advertise();
  Serial.println("BLE is now advertising...");
}
 
void loop() {
  //look for STOP command
  //this can be changed for a button press or app signal
  if (Serial.available()) {
  String command = Serial.readStringUntil('/n');
  command.trim();
    if (command == "STOP") {
    stopFlag = true;
    printArray();
    return;
    }
  }
  BLEDevice central = BLE.central();
  if (central) {
    Serial.print("Connected to: "); 
    Serial.println(central.address());
  }
  //define the IMU variables
  float gx, gy, gz;
  float ax, ay, az;
 // check IMU is available and read
  if (IMU.gyroscopeAvailable()) {
    IMU.readGyroscope(gx, gy, gz);
    IMU.readAcceleration(ax, ay, az);
 
    unsigned long currentTime = millis();
    float gyroRate = gz; //assuming z axis rotation
    float accelAngle = atan2(az, sqrt(ax * ax + ay * ay)) * 180.0 / PI; //nullifies any error in Z from the arduino orientation
    if (az < 0){
      accelAngle = (accelAngle > 0) ? (180 - accelAngle) : (-180 - accelAngle); //make the angle between 180s
    }

    //kalman predict
    angle += dtt * (gyroRate - bias);
    P[0][0]+= dtt * (dtt*P[1][1] - P[0][1] - P[1][0] + Q_angle);
    P[0][1] -= dtt * P[1][1];
    P[1][0] -= dtt * P[1][1];
    P[1][1] += Q_bias * dtt;

    //kalman update
    float S = P[0][0] + R_measure+ 1e-6;
    float K[2];
    K[0] = P[0][0] / S;
    K[1] = P[1][0] / S;

    float y = accelAngle - angle;
    angle += K[0] * y;
    bias += K[1] * y;


    float P00_temp = P[0][0];
    float P01_temp = P[0][1];
    P[0][0] -= K[0] * P[0][0];
    P[0][1] -= K[0] * P[0][1];
    P[1][0] -= K[1] * P00_temp;
    P[1][1] -= K[1] * P01_temp;
    angleKnee = angle;

    if (angleKnee > 180){
      angleKnee = -180 + (angleKnee - 180);
    }
    else if (angleKnee < -180){
    angleKnee = 180 - (abs(angleKnee) - 180);
    }
    Serial.print("Knee Angle: ");
    Serial.println(angleKnee);
    kneeAngleCharacteristic.writeValue(angleKnee);
    //Serial.println(gz);
    if (angleKnee > threshold) { //if numerically above but the flag hasnt been thrown
      if (!aboveThreshold) {
        // just crossed threshold
        aboveThreshold = true;
        peakValue = angleKnee;
        for (int i = 0; i < 3; i++){ //buzz three times
//          drv.setWaveform(0, 47); //effect ID 47 means short buzz
//          drv.setWaveform(1,0);
//          drv.go();
//          delay(300);
        }
      } else {
        //keep updating peak
        if (angleKnee > peakValue) {
          peakValue = angleKnee;
        }
      }
    } else {
      if (aboveThreshold) {
        // just went back below
        Serial.print("Peak Value Recorded: ");
        Serial.println(peakValue);
        aboveThreshold = false;
        anArray[arrayIndex] = peakValue;
        arrayIndex++;
        peakValue = 0.0; // reset
      }
    //Serial.print("threshold: ");
    //Serial.println(threshold);
    }
  }
  delay(50); // Small delay for stable readings
}
