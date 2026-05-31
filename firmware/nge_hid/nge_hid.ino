// NGE HID firmware for ESP32-S3
//
// Exposes a USB HID composite device (absolute mouse + keyboard) plus a USB CDC
// serial command channel. The host (Python) sends atomic text commands; all
// human-like motion (bezier paths, easing, jitter, random timing) is generated
// on the host side and streamed here as a series of absolute moves.
//
// Arduino IDE settings (Tools menu):
//   Board:           ESP32S3 Dev Module
//   USB Mode:        USB-OTG (TinyUSB)
//   USB CDC On Boot: Enabled
//
// Protocol (one command per line, terminated by \n; each line is answered):
//   MA <x> <y>        absolute move, x,y in [0,32767]   -> OK
//   BTN <L|R|M> <0|1> mouse button up(0)/down(1)        -> OK
//   CLK <L|R|M> [ms]  press, hold ms (default 20), up    -> OK
//   WHEEL <delta>     wheel, -127..127                   -> OK
//   KD <code>         key down (HID usage id, 0x.. ok)   -> OK
//   KU <code>         key up                             -> OK
//   KP <code> [ms]    key press (default 20ms)           -> OK
//   MOD <byte>        set modifier bitmask               -> OK
//   PING                                                 -> PONG
//   STOP              release all keys/buttons           -> OK
// Unknown / malformed input answers ERR.

#include "USB.h"
#include "USBHID.h"

// Report ID 1 = absolute mouse, Report ID 2 = keyboard.
static const uint8_t REPORT_DESCRIPTOR[] = {
  // ---- Absolute Mouse (Report ID 1) ----
  0x05, 0x01,        // Usage Page (Generic Desktop)
  0x09, 0x02,        // Usage (Mouse)
  0xA1, 0x01,        // Collection (Application)
    0x85, 0x01,      //   Report ID (1)
    0x09, 0x01,      //   Usage (Pointer)
    0xA1, 0x00,      //   Collection (Physical)
      0x05, 0x09,    //     Usage Page (Buttons)
      0x19, 0x01, 0x29, 0x03,
      0x15, 0x00, 0x25, 0x01,
      0x95, 0x03, 0x75, 0x01, 0x81, 0x02,   // 3 buttons
      0x95, 0x01, 0x75, 0x05, 0x81, 0x03,   // 5-bit padding
      0x05, 0x01,    //     Usage Page (Generic Desktop)
      0x09, 0x30,    //     Usage (X)
      0x09, 0x31,    //     Usage (Y)
      0x16, 0x00, 0x00,        // Logical Min (0)
      0x26, 0xFF, 0x7F,        // Logical Max (32767)
      0x75, 0x10, 0x95, 0x02, 0x81, 0x02,   // X,Y absolute 16-bit
      0x09, 0x38,    //     Usage (Wheel)
      0x15, 0x81, 0x25, 0x7F,
      0x75, 0x08, 0x95, 0x01, 0x81, 0x06,   // wheel relative
    0xC0,
  0xC0,
  // ---- Keyboard (Report ID 2) ----
  0x05, 0x01, 0x09, 0x06, 0xA1, 0x01,
    0x85, 0x02,      //   Report ID (2)
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7,
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02, // modifiers
    0x95, 0x01, 0x75, 0x08, 0x81, 0x03,                          // reserved
    0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0xFF,
    0x05, 0x07, 0x19, 0x00, 0x29, 0xFF, 0x81, 0x00,              // 6 keycodes
  0xC0
};

static const uint8_t RID_MOUSE = 1;
static const uint8_t RID_KB    = 2;

USBHID HID;

class CompositeHID : public USBHIDDevice {
public:
  CompositeHID() { HID.addDevice(this, sizeof(REPORT_DESCRIPTOR)); }
  void begin() { HID.begin(); }

  uint16_t _onGetDescriptor(uint8_t* dst) override {
    memcpy(dst, REPORT_DESCRIPTOR, sizeof(REPORT_DESCRIPTOR));
    return sizeof(REPORT_DESCRIPTOR);
  }

  bool sendMouse(uint8_t buttons, uint16_t x, uint16_t y, int8_t wheel) {
    uint8_t r[6] = { buttons,
                     (uint8_t)(x & 0xFF), (uint8_t)(x >> 8),
                     (uint8_t)(y & 0xFF), (uint8_t)(y >> 8),
                     (uint8_t)wheel };
    return HID.SendReport(RID_MOUSE, r, sizeof(r));
  }

  bool sendKeyboard(uint8_t mod, const uint8_t* keys6) {
    uint8_t r[8] = { mod, 0, keys6[0], keys6[1], keys6[2], keys6[3], keys6[4], keys6[5] };
    return HID.SendReport(RID_KB, r, sizeof(r));
  }
};

CompositeHID Device;

// ---- Live device state ----
static uint8_t  g_buttons = 0;
static uint16_t g_x = 16384, g_y = 16384;
static uint8_t  g_mod  = 0;
static uint8_t  g_keys[6] = {0, 0, 0, 0, 0, 0};

static const int STOP_PIN = 0;   // BOOT button -> emergency release

static void pushMouse(int8_t wheel = 0) { Device.sendMouse(g_buttons, g_x, g_y, wheel); }
static void pushKeyboard()              { Device.sendKeyboard(g_mod, g_keys); }

static uint8_t btnBit(char c) {
  if (c == 'L' || c == 'l') return 0x01;
  if (c == 'R' || c == 'r') return 0x02;
  if (c == 'M' || c == 'm') return 0x04;
  return 0;
}

static void keyDown(uint8_t code) {
  for (int i = 0; i < 6; i++) if (g_keys[i] == code) return;   // already held
  for (int i = 0; i < 6; i++) if (g_keys[i] == 0) { g_keys[i] = code; break; }
  pushKeyboard();
}

static void keyUp(uint8_t code) {
  for (int i = 0; i < 6; i++) if (g_keys[i] == code) g_keys[i] = 0;
  pushKeyboard();
}

static void releaseAll() {
  g_buttons = 0;
  g_mod = 0;
  for (int i = 0; i < 6; i++) g_keys[i] = 0;
  pushMouse();
  pushKeyboard();
}

static long clampL(long v, long lo, long hi) { return v < lo ? lo : (v > hi ? hi : v); }

static void handleLine(char* line) {
  char* cmd = strtok(line, " \t\r\n");
  if (!cmd) return;

  if (!strcmp(cmd, "MA")) {
    char* sx = strtok(NULL, " \t\r\n");
    char* sy = strtok(NULL, " \t\r\n");
    if (sx && sy) {
      g_x = (uint16_t)clampL(strtol(sx, NULL, 10), 0, 32767);
      g_y = (uint16_t)clampL(strtol(sy, NULL, 10), 0, 32767);
      pushMouse();
      Serial.println("OK");
    } else Serial.println("ERR");

  } else if (!strcmp(cmd, "BTN")) {
    char* b = strtok(NULL, " \t\r\n");
    char* s = strtok(NULL, " \t\r\n");
    if (b && s) {
      uint8_t bit = btnBit(b[0]);
      if (s[0] == '1') g_buttons |= bit; else g_buttons &= ~bit;
      pushMouse();
      Serial.println("OK");
    } else Serial.println("ERR");

  } else if (!strcmp(cmd, "CLK")) {
    char* b = strtok(NULL, " \t\r\n");
    char* m = strtok(NULL, " \t\r\n");
    if (b) {
      uint8_t bit = btnBit(b[0]);
      uint32_t ms = m ? strtoul(m, NULL, 10) : 20;
      g_buttons |= bit;  pushMouse();
      delay(ms);
      g_buttons &= ~bit; pushMouse();
      Serial.println("OK");
    } else Serial.println("ERR");

  } else if (!strcmp(cmd, "WHEEL")) {
    char* d = strtok(NULL, " \t\r\n");
    if (d) { pushMouse((int8_t)strtol(d, NULL, 10)); Serial.println("OK"); }
    else Serial.println("ERR");

  } else if (!strcmp(cmd, "KD")) {
    char* k = strtok(NULL, " \t\r\n");
    if (k) { keyDown((uint8_t)strtol(k, NULL, 0)); Serial.println("OK"); }
    else Serial.println("ERR");

  } else if (!strcmp(cmd, "KU")) {
    char* k = strtok(NULL, " \t\r\n");
    if (k) { keyUp((uint8_t)strtol(k, NULL, 0)); Serial.println("OK"); }
    else Serial.println("ERR");

  } else if (!strcmp(cmd, "KP")) {
    char* k = strtok(NULL, " \t\r\n");
    char* m = strtok(NULL, " \t\r\n");
    if (k) {
      uint8_t code = (uint8_t)strtol(k, NULL, 0);
      uint32_t ms = m ? strtoul(m, NULL, 10) : 20;
      keyDown(code); delay(ms); keyUp(code);
      Serial.println("OK");
    } else Serial.println("ERR");

  } else if (!strcmp(cmd, "MOD")) {
    char* m = strtok(NULL, " \t\r\n");
    if (m) { g_mod = (uint8_t)strtol(m, NULL, 0); pushKeyboard(); Serial.println("OK"); }
    else Serial.println("ERR");

  } else if (!strcmp(cmd, "PING")) {
    Serial.println("PONG");

  } else if (!strcmp(cmd, "STOP")) {
    releaseAll();
    Serial.println("OK");

  } else {
    Serial.println("ERR");
  }
}

static char   g_buf[160];
static size_t g_len = 0;

void setup() {
  pinMode(STOP_PIN, INPUT_PULLUP);
  Device.begin();
  USB.begin();
  Serial.begin(115200);   // USB CDC command channel
}

void loop() {
  if (digitalRead(STOP_PIN) == LOW) releaseAll();   // emergency stop

  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (g_len) { g_buf[g_len] = 0; handleLine(g_buf); g_len = 0; }
    } else if (g_len < sizeof(g_buf) - 1) {
      g_buf[g_len++] = c;
    }
  }
}
