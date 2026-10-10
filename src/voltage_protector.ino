
#include <Arduino.h>
#include <math.h>
#include <string.h>
#include <stdlib.h>


const int VOLTAGE_PIN = A0;
const int CURRENT_PIN = A1;
const int RELAY_PIN   = 8;
const int RESET_PIN   = 7;


const float ADC_REF = 5.0;


const float TRANSFORMER_RATIO = 44.0;
const float VOLTAGE_CAL = 220.0 / 217.14;

const float R1 = 100000.0;
const float R2 = 27000.0;
const float R3 = 100000.0;

const float VOLTAGE_GAIN =
  (1.0 / R1) /
  (1.0 / R1 + 1.0 / R2 + 1.0 / R3);

const float SHUNT = 4.7;
const float CURRENT_GAIN = 1.0 / 3.0;

float OV_LIMIT = 250.0;
float UV_LIMIT = 180.0;


float gridVoltage = 0;
float loadCurrent = 0;


enum State {
  STARTUP,
  NORMAL,
  OVERVOLTAGE_LATCHED,
  UNDERVOLTAGE_LATCHED
};

State state = STARTUP;

bool lastReset = false;

unsigned long lastUpdate = 0;

// Serial command buffer
char command[40];
int cmdIndex = 0;


float readACRMS(int pin) {

  const int N = 400;

  double sum = 0;
  double sumSq = 0;

  analogRead(pin);

  for (int i = 0; i < N; i++) {

    float v =
      analogRead(pin) * ADC_REF / 1023.0;

    sum += v;
    sumSq += v * v;

    delayMicroseconds(500);
  }

  double mean = sum / N;

  double variance =
    sumSq / N - mean * mean;

  if (variance < 0) variance = 0;

  return sqrt(variance);
}


void measureValues() {

  float voltageADC = readACRMS(VOLTAGE_PIN);
  float currentADC = readACRMS(CURRENT_PIN);

  gridVoltage =
    voltageADC / VOLTAGE_GAIN *
    TRANSFORMER_RATIO * VOLTAGE_CAL;

  loadCurrent =
    currentADC / (CURRENT_GAIN * SHUNT);
}


const char* stateName() {

  switch (state) {

    case STARTUP:
      return "STARTUP";

    case NORMAL:
      return "NORMAL";

    case OVERVOLTAGE_LATCHED:
      return "OVERVOLTAGE_LATCHED";

    case UNDERVOLTAGE_LATCHED:
      return "UNDERVOLTAGE_LATCHED";
  }

  return "UNKNOWN";
}


void controlRelay() {

  digitalWrite(
    RELAY_PIN,
    state == NORMAL ? HIGH : LOW
  );
}


void showFault(const char* fault) {

  Serial.println();
  Serial.println("FAULT !!!");
  Serial.println(fault);
  Serial.println();
}


void checkProtection() {


  if (state == OVERVOLTAGE_LATCHED ||
      state == UNDERVOLTAGE_LATCHED) {
    return;
  }

  if (gridVoltage > OV_LIMIT) {

    state = OVERVOLTAGE_LATCHED;

    controlRelay();

    showFault("OVERVOLTAGE");
  }

  else if (gridVoltage < UV_LIMIT) {

    state = UNDERVOLTAGE_LATCHED;

    controlRelay();

    showFault("UNDERVOLTAGE");
  }

  else if (state == STARTUP) {

    // Start only within the safe range
    if (gridVoltage >= UV_LIMIT + 10 &&
        gridVoltage <= OV_LIMIT - 5) {

      state = NORMAL;
    }
  }
}


void resetFault() {

  measureValues();

  // Reset only if voltage is safe
  if (gridVoltage < UV_LIMIT + 10 ||
      gridVoltage > OV_LIMIT - 5) {

    Serial.println("RESET DENIED");
    return;
  }

  state = NORMAL;

  controlRelay();

  Serial.println("RESET SUCCESSFUL");
}


void displayValues() {

  // Human-readable output
  Serial.println();

  Serial.print("Grid Voltage = ");
  Serial.print(gridVoltage, 2);
  Serial.println(" V");

  Serial.print("Load Current = ");
  Serial.print(loadCurrent, 3);
  Serial.println(" A");

  if (state == NORMAL) {
    Serial.println("Condition = NORMAL");
  }
  else if (state == OVERVOLTAGE_LATCHED) {
    Serial.println("Condition = OVERVOLTAGE FAULT");
  }
  else if (state == UNDERVOLTAGE_LATCHED) {
    Serial.println("Condition = UNDERVOLTAGE FAULT");
  }

  Serial.print("GRID=");
  Serial.print(gridVoltage, 2);

  Serial.print(" V | RELAY=");
  Serial.print(state == NORMAL ? "ON" : "OFF");

  Serial.print(" | STATE=");
  Serial.println(stateName());

  Serial.print("UV_LIMIT=");
  Serial.print(UV_LIMIT, 1);

  Serial.print(" V | OV_LIMIT=");
  Serial.print(OV_LIMIT, 1);

  Serial.println(" V");
}


void processCommand() {

  command[cmdIndex] = '\0';
  cmdIndex = 0;


  for (int i = 0; command[i]; i++) {
    if (command[i] >= 'a' &&
        command[i] <= 'z') {
      command[i] -= 32;
    }
  }

  if (strcmp(command, "STATUS") == 0) {

    measureValues();
    checkProtection();
    controlRelay();
    displayValues();
  }

  else if (strcmp(command, "RESET") == 0) {

    resetFault();
  }

  else if (strcmp(command, "HELP") == 0) {

    Serial.println("COMMANDS:");
    Serial.println("STATUS");
    Serial.println("RESET");
    Serial.println("SET OV 250");
    Serial.println("SET UV 180");
    Serial.println("HELP");
  }

  else if (strncmp(command, "SET OV ", 7) == 0) {

    char* end;
    float value = strtod(command + 7, &end);

    if (end == command + 7 || *end != '\0' ||
        !isfinite(value) ||
        value < 210 || value > 280 ||
        value <= UV_LIMIT + 20) {

      Serial.println("INVALID OV LIMIT");
      return;
    }

    OV_LIMIT = value;

    Serial.println("OV LIMIT UPDATED");

    measureValues();
    checkProtection();
    controlRelay();
    displayValues();
  }

  else if (strncmp(command, "SET UV ", 7) == 0) {

    char* end;
    float value = strtod(command + 7, &end);

    if (end == command + 7 || *end != '\0' ||
        !isfinite(value) ||
        value < 100 || value > 220 ||
        value >= OV_LIMIT - 20) {

      Serial.println("INVALID UV LIMIT");
      return;
    }

    UV_LIMIT = value;

    Serial.println("UV LIMIT UPDATED");

    measureValues();
    checkProtection();
    controlRelay();
    displayValues();
  }

  else {
    Serial.println("UNKNOWN COMMAND");
  }
}


void readCommands() {

  while (Serial.available()) {

    char c = Serial.read();

    if (c == '\n' || c == '\r') {

      if (cmdIndex > 0) {
        processCommand();
      }
    }

    else if (cmdIndex < 39) {

      command[cmdIndex++] = c;
    }

    else {
      cmdIndex = 0;
      Serial.println("COMMAND TOO LONG");
    }
  }
}


void setup() {

  digitalWrite(RELAY_PIN, LOW);
  pinMode(RELAY_PIN, OUTPUT);

  pinMode(RESET_PIN, INPUT_PULLUP);

  Serial.begin(9600);

  Serial.println("FSM AC VOLTAGE PROTECTOR");
  Serial.println("SYSTEM STARTED");
}


void loop() {


  readCommands();

  // Manual pushbutton reset
  bool pressed =
    digitalRead(RESET_PIN) == LOW;

  if (pressed && !lastReset) {

    resetFault();
  }

  lastReset = pressed;


  if (millis() - lastUpdate >= 500) {

    lastUpdate = millis();

    measureValues();

    checkProtection();

    controlRelay();

    displayValues();
  }
}
