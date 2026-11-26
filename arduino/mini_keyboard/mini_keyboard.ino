/*
 * Mini Keyboard Configurator - Firmware
 * Boards:
 *  - Arduino Micro / Pro Micro / Leonardo (ATmega32U4): native USB, acts as a
 *    HID keyboard by itself, so shortcuts and text macros also work with the
 *    desktop app closed.
 *  - Arduino Nano / Uno (ATmega328P): no native USB (a USB-serial bridge chip
 *    sits between the MCU and the PC), so it cannot be a HID keyboard. It only
 *    reports key presses over serial and the desktop app performs every action.
 * Features:
 *  - Memory Optimized 5-Mode Multi-Profile Engine (active mode count set from the GUI)
 *  - Action Types: SHORTCUT(1), TEXT(2), MEDIA(3), MODE_SWITCH(4), APP_LAUNCH(5)
 *  - Non-Blocking Debounced Key Handling with Single & Long Press
 */

#include <Arduino.h>
#include <EEPROM.h>
// USBCON is defined by the AVR core only on MCUs with a USB controller (32U4).
#if defined(USBCON)
#define HAS_HID 1
#include <Keyboard.h>
#else
#define HAS_HID 0
#endif
#include <ctype.h>
#include <stddef.h>
#include <stdlib.h>

// Bump this whenever DeviceConfig's layout changes so old, incompatible
// EEPROM bytes get discarded instead of being misread as the new struct.
#define EEPROM_MAGIC 0x4D4C21
#define EEPROM_START_ADDR 0

const uint8_t KEY_PINS[9] = {7, 8, 9, 4, 5, 6, 10, 2, 3};

enum ActionType : uint8_t {
  ACTION_NONE = 0,
  ACTION_SHORTCUT = 1,
  ACTION_TEXT = 2,
  ACTION_MEDIA = 3,
  ACTION_MODE_SWITCH = 4,
  ACTION_APP = 5
};

#define MOD_CTRL  (1 << 0)
#define MOD_SHIFT (1 << 1)
#define MOD_ALT   (1 << 2)
#define MOD_GUI   (1 << 3)

#define MAX_MODES 5

// Text buffer sized so the full config (MAX_MODES modes x 9 keys x 2 actions)
// fits inside the ATmega32U4's 1KB EEPROM. See static_assert below.
#define MACRO_TEXT_LEN 8

struct ActionConfig {
  uint8_t actionType;
  uint8_t modifiers;
  // Holds the shortcut key code (ACTION_SHORTCUT) or the media command code
  // (ACTION_MEDIA) - only one is ever relevant for a given actionType, so
  // they share the byte to leave more EEPROM room for macro text.
  uint8_t keyCode;
  char text[MACRO_TEXT_LEN];
};

struct KeyConfig {
  ActionConfig singleClick;
  ActionConfig longPress;
};

// EEPROM layout. Only used for sizeof/offsetof - the whole struct is no
// longer kept in RAM. It used to be a ~1KB global (39% of the 2.5KB SRAM,
// arduino-cli warned "Low memory available"), although only the active
// mode's 9 keys are ever needed at once. Now RAM holds the header plus the
// active mode (~200 bytes) and other modes are read from EEPROM when the
// mode changes. The byte layout is unchanged, so boards flashed with the
// old firmware keep their saved keys.
struct DeviceConfig {
  uint32_t magic;
  uint8_t currentMode;
  uint8_t modeCount;
  KeyConfig modes[MAX_MODES][9];
};

static_assert(sizeof(DeviceConfig) <= 1024, "DeviceConfig no longer fits in the ATmega32U4's 1KB EEPROM - shrink MACRO_TEXT_LEN or MAX_MODES.");

struct ConfigHeader {
  uint32_t magic;
  uint8_t currentMode;
  uint8_t modeCount;
} hdr;

KeyConfig keys[9];   // active mode only

#define KEY_ADDR(mode, key) (EEPROM_START_ADDR + offsetof(DeviceConfig, modes) + \
                             ((mode) * 9 + (key)) * sizeof(KeyConfig))

void setAction(ActionConfig& act, uint8_t type, uint8_t mods, uint8_t key, const char* txt) {
  act.actionType = type;
  act.modifiers = mods;
  act.keyCode = key;
  if (txt != NULL) {
    strncpy(act.text, txt, sizeof(act.text) - 1);
    act.text[sizeof(act.text) - 1] = '\0';
  } else {
    act.text[0] = '\0';
  }
}

// Forward Declarations
void loadConfig();
void saveConfig();
void loadModeKeys();
void setDefaultConfig();
void executeAction(const ActionConfig& act);
void executeShortcut(uint8_t mods, uint8_t keyCode);
void handleSerialNonBlocking();
void processSerialLine(char* line);
void parseIncomingConfig(char* line);
void switchNextMode();

void notifyKeyPress(int id, const char* pressType) {
  Serial.print(F("{\"event\":\"KEY_PRESS\",\"id\":"));
  Serial.print(id);
  Serial.print(F(",\"type\":\""));
  Serial.print(pressType);
  Serial.println(F("\"}"));
  Serial.flush();
}

uint8_t activeModeCount() {
  if (hdr.modeCount < 1 || hdr.modeCount > MAX_MODES) return MAX_MODES;
  return hdr.modeCount;
}

// Switches the active mode: the header is saved and the new mode's keys are
// loaded from EEPROM. The previous mode's keys need no save here - they are
// only ever changed by SET_CONFIG/SET_KEY, which saves them immediately.
void selectMode(uint8_t mode) {
  mode %= activeModeCount();
  if (mode != hdr.currentMode) {
    hdr.currentMode = mode;
    EEPROM.put(EEPROM_START_ADDR, hdr);
    loadModeKeys();
  }
}

void switchNextMode() {
  selectMode((hdr.currentMode + 1) % activeModeCount());

  Serial.print(F("{\"event\":\"MODE_CHANGE\",\"mode\":"));
  Serial.print(hdr.currentMode);
  Serial.println(F("}"));
  Serial.flush();
}

class SimpleButton {
public:
  uint8_t pin;
  uint8_t id;
  bool buttonState;
  bool lastReading;
  unsigned long lastDebounceTime;
  unsigned long pressStartTime;
  bool longPressTriggered;

  SimpleButton() : pin(0), id(0), buttonState(HIGH), lastReading(HIGH),
                   lastDebounceTime(0), pressStartTime(0), longPressTriggered(false) {}

  void init(uint8_t p, uint8_t key_id) {
    pin = p;
    id = key_id;
    pinMode(pin, INPUT_PULLUP);
    buttonState = digitalRead(pin);
    lastReading = buttonState;
    lastDebounceTime = 0;
    pressStartTime = 0;
    longPressTriggered = false;
  }

  void update() {
    bool reading = digitalRead(pin);
    unsigned long now = millis();

    if (reading != lastReading) {
      lastDebounceTime = now;
    }

    if ((now - lastDebounceTime) > 15) {
      if (reading != buttonState) {
        buttonState = reading;
        
        if (buttonState == LOW) {
          pressStartTime = now;
          longPressTriggered = false;
          notifyKeyPress(id, "raw_down");
        }
        else if (buttonState == HIGH) {
          notifyKeyPress(id, "raw_up");
          unsigned long holdDuration = now - pressStartTime;
          if (!longPressTriggered && holdDuration < 350 && holdDuration > 10) {
            notifyKeyPress(id, "down");
            // Only run the action on-device when no PC app has the serial
            // port open. When the Configurator is connected it executes the
            // action itself (it also handles types the AVR can't, like
            // media keys and app launches) - running it here too would fire
            // every shortcut/text macro twice.
            if (!Serial) {
              executeAction(keys[id].singleClick);
            }
          }
        }
      }
    }

    if (buttonState == LOW && !longPressTriggered && (now - pressStartTime > 350)) {
      longPressTriggered = true;
      notifyKeyPress(id, "long");
      if (!Serial) {
        executeAction(keys[id].longPress);
      }
    }

    lastReading = reading;
  }
};

SimpleButton btnArray[9];

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(5);
#if HAS_HID
  Keyboard.begin();
#endif

  loadConfig();

  for (int i = 0; i < 9; i++) {
    btnArray[i].init(KEY_PINS[i], i);
  }
}

void loop() {
  for (int i = 0; i < 9; i++) {
    btnArray[i].update();
  }

  handleSerialNonBlocking();
}

void executeAction(const ActionConfig& act) {
  if (act.actionType == ACTION_NONE) return;

#if HAS_HID
  if (act.actionType == ACTION_SHORTCUT) {
    executeShortcut(act.modifiers, act.keyCode);
  }
  else if (act.actionType == ACTION_TEXT) {
    Keyboard.releaseAll();
    delay(10);
    Keyboard.print(act.text);
    delay(20);
  }
  else
#endif
  if (act.actionType == ACTION_MODE_SWITCH) {
    switchNextMode();
  }
}

#if HAS_HID
// The GUI captures shortcut keys as Windows Virtual-Key codes (so arrows,
// F-keys, etc. can be recorded, not just single printable characters). The
// Arduino Keyboard library needs its own KEY_* HID codes for those same
// non-printable keys, so translate before pressing. Printable ASCII
// letters/digits pass straight through unchanged.
uint8_t vkToArduinoKey(uint8_t vk) {
  // Letters are stored as uppercase VK codes ('A' = 0x41). The Keyboard
  // library treats an uppercase char as "Shift + letter", so Ctrl+C used to
  // go out as Ctrl+Shift+C. Shift is a modifier bit of its own.
  if (vk >= 'A' && vk <= 'Z') return vk + ('a' - 'A');
  switch (vk) {
    // Punctuation keys: Windows OEM VK codes -> the US character on the same
    // physical key (the Keyboard library maps chars through a US layout).
    case 0xBA: return ';';
    case 0xBB: return '=';
    case 0xBC: return ',';
    case 0xBD: return '-';
    case 0xBE: return '.';
    case 0xBF: return '/';
    case 0xC0: return '`';
    case 0xDB: return '[';
    case 0xDC: return '\\';
    case 0xDD: return ']';
    case 0xDE: return '\'';
    case 0x25: return KEY_LEFT_ARROW;
    case 0x26: return KEY_UP_ARROW;
    case 0x27: return KEY_RIGHT_ARROW;
    case 0x28: return KEY_DOWN_ARROW;
    case 0x1B: return KEY_ESC;
    case 0x09: return KEY_TAB;
    case 0x0D: return KEY_RETURN;
    case 0x08: return KEY_BACKSPACE;
    case 0x2E: return KEY_DELETE;
    case 0x24: return KEY_HOME;
    case 0x23: return KEY_END;
    case 0x21: return KEY_PAGE_UP;
    case 0x22: return KEY_PAGE_DOWN;
    case 0x70: return KEY_F1;
    case 0x71: return KEY_F2;
    case 0x72: return KEY_F3;
    case 0x73: return KEY_F4;
    case 0x74: return KEY_F5;
    case 0x75: return KEY_F6;
    case 0x76: return KEY_F7;
    case 0x77: return KEY_F8;
    case 0x78: return KEY_F9;
    case 0x79: return KEY_F10;
    case 0x7A: return KEY_F11;
    case 0x7B: return KEY_F12;
    default: return vk;
  }
}

void executeShortcut(uint8_t mods, uint8_t keyCode) {
  Keyboard.releaseAll();
  delay(10);

  if (mods & MOD_CTRL)  Keyboard.press(KEY_LEFT_CTRL);
  if (mods & MOD_SHIFT) Keyboard.press(KEY_LEFT_SHIFT);
  if (mods & MOD_ALT)   Keyboard.press(KEY_LEFT_ALT);
  if (mods & MOD_GUI)   Keyboard.press(KEY_LEFT_GUI);

  if (keyCode != 0) {
    Keyboard.press(vkToArduinoKey(keyCode));
  }

  delay(80);
  Keyboard.releaseAll();
  delay(20);
}
#endif

void loadModeKeys() {
  for (uint8_t k = 0; k < 9; k++) {
    EEPROM.get(KEY_ADDR(hdr.currentMode, k), keys[k]);
  }
}

void loadConfig() {
  EEPROM.get(EEPROM_START_ADDR, hdr);
  if (hdr.magic != EEPROM_MAGIC) {
    setDefaultConfig();
    return;
  }
  hdr.currentMode %= activeModeCount();
  loadModeKeys();
}

// Saves the header and the active mode's keys. EEPROM.put() only rewrites
// bytes that actually changed, so an unchanged key costs no EEPROM wear.
void saveConfig() {
  hdr.magic = EEPROM_MAGIC;
  EEPROM.put(EEPROM_START_ADDR, hdr);
  for (uint8_t k = 0; k < 9; k++) {
    EEPROM.put(KEY_ADDR(hdr.currentMode, k), keys[k]);
  }
}

void setDefaultConfig() {
  hdr.magic = EEPROM_MAGIC;
  hdr.currentMode = 0;
  hdr.modeCount = 3;

  memset(keys, 0, sizeof(keys));
  for (uint8_t m = 1; m < MAX_MODES; m++) {
    for (uint8_t k = 0; k < 9; k++) {
      EEPROM.put(KEY_ADDR(m, k), keys[k]);
    }
  }
  setAction(keys[0].singleClick, ACTION_MEDIA, 0, 6, "");
  setAction(keys[1].singleClick, ACTION_MEDIA, 0, 4, "");
  setAction(keys[2].singleClick, ACTION_MEDIA, 0, 5, "");
  saveConfig();
}

// Wire-format action type codes (matches the GUI's ACTION_TYPE_TO_WIRE map):
// n=none, s=shortcut, x=text, d=media, w=mode_switch, a=app. Kept as single
// chars because these, and the field names below (i/s/l/t/m/k/c/v), repeat
// up to 18 times in one SET_CONFIG line - see the RX_BUFFER_SIZE comment
// for why that repetition is what forced this rewrite.
void printActionJson(const ActionConfig& act) {
  if (act.actionType == ACTION_SHORTCUT) {
    Serial.print(F("{\"t\":\"s\",\"m\":"));
    Serial.print(act.modifiers);
    Serial.print(F(",\"k\":"));
    Serial.print(act.keyCode);
    Serial.print(F("}"));
  } else if (act.actionType == ACTION_TEXT) {
    Serial.print(F("{\"t\":\"x\",\"v\":\""));
    Serial.print(act.text);
    Serial.print(F("\"}"));
  } else if (act.actionType == ACTION_MEDIA) {
    Serial.print(F("{\"t\":\"d\",\"c\":"));
    Serial.print(act.keyCode);
    Serial.print(F("}"));
  } else if (act.actionType == ACTION_MODE_SWITCH) {
    Serial.print(F("{\"t\":\"w\"}"));
  } else if (act.actionType == ACTION_APP) {
    Serial.print(F("{\"t\":\"a\",\"v\":\""));
    Serial.print(act.text);
    Serial.print(F("\"}"));
  } else {
    Serial.print(F("{\"t\":\"n\"}"));
  }
}

void sendFullConfigSerial() {
  Serial.print(F("{\"status\":\"OK\",\"mode\":"));
  Serial.print(hdr.currentMode);
  Serial.print(F(",\"modeCount\":"));
  Serial.print(activeModeCount());
  Serial.print(F(",\"keys\":["));
  for (int i = 0; i < 9; i++) {
    if (i > 0) Serial.print(F(","));
    Serial.print(F("{\"i\":"));
    Serial.print(i);
    Serial.print(F(",\"s\":"));
    printActionJson(keys[i].singleClick);
    Serial.print(F(",\"l\":"));
    printActionJson(keys[i].longPress);
    Serial.print(F("}"));
  }
  Serial.println(F("]}"));
}

// Parses the JSON action object found between [start, end) inside rxBuffer
// in place - no String, no heap allocation. end is temporarily NUL'd so the
// C string search functions (strstr/strchr) stay bounded to this fragment;
// we own that memory (it's rxBuffer) for the duration of this call, so it's
// restored before returning.
void parseActionString(char* start, char* end, ActionConfig& act) {
  memset(&act, 0, sizeof(ActionConfig));
  char saved = *end;
  *end = '\0';

  if (strstr(start, "\"t\":\"s\"")) {
    act.actionType = ACTION_SHORTCUT;
    char* modsPtr = strstr(start, "\"m\":");
    if (modsPtr) act.modifiers = atoi(modsPtr + 4);
    char* keyPtr = strstr(start, "\"k\":");
    if (keyPtr) act.keyCode = atoi(keyPtr + 4);
  } else if (strstr(start, "\"t\":\"x\"") || strstr(start, "\"t\":\"a\"")) {
    // Both text macros and app paths are just a quoted string in field "v" -
    // same extraction, only actionType differs.
    act.actionType = strstr(start, "\"t\":\"x\"") ? ACTION_TEXT : ACTION_APP;
    char* vPtr = strstr(start, "\"v\":");
    if (vPtr) {
      char* q1 = strchr(vPtr + 4, '"');
      if (q1) {
        char* q2 = strchr(q1 + 1, '"');
        if (q2) {
          size_t len = q2 - (q1 + 1);
          if (len > sizeof(act.text) - 1) len = sizeof(act.text) - 1;
          memcpy(act.text, q1 + 1, len);
          act.text[len] = '\0';
        }
      }
    }
  } else if (strstr(start, "\"t\":\"d\"")) {
    act.actionType = ACTION_MEDIA;
    char* codePtr = strstr(start, "\"c\":");
    if (codePtr) act.keyCode = atoi(codePtr + 4);
  } else if (strstr(start, "\"t\":\"w\"")) {
    act.actionType = ACTION_MODE_SWITCH;
  } else {
    act.actionType = ACTION_NONE;
  }

  *end = saved;
}

// Fixed, non-heap serial line buffer - replaces the old Arduino String
// rxBuffer. String reallocated on every appended character (and every
// substring() call in the parser used to allocate again), which on this
// chip's 2560-byte SRAM risks heap fragmentation over long uptimes with
// repeated SET_CONFIG saves.
//
// The GUI sends the short wire format (i/s/l/t/m/k/c/v field names,
// single-char type codes - see printActionJson/parseActionString), at most
// 3 keys per line (SET_CONFIG, then SET_KEY), with macro text cut to the 7
// chars stored here and no app paths. The worst case is ~240 bytes, so 512
// leaves a wide margin; a line that still overflows is drained and rejected
// below instead of being silently truncated into a half-valid config.
#define RX_BUFFER_SIZE 512
char rxBuffer[RX_BUFFER_SIZE];
uint16_t rxLen = 0;
bool rxOverflow = false;

void handleSerialNonBlocking() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') {
      if (rxOverflow) {
        Serial.println(F("{\"status\":\"ERROR\",\"message\":\"Line too long for device buffer\"}"));
        rxOverflow = false;
      } else {
        rxBuffer[rxLen] = '\0';
        while (rxLen > 0 && isspace((unsigned char)rxBuffer[rxLen - 1])) {
          rxBuffer[--rxLen] = '\0';
        }
        char* trimmedStart = rxBuffer;
        while (*trimmedStart && isspace((unsigned char)*trimmedStart)) trimmedStart++;
        if (*trimmedStart != '\0') {
          processSerialLine(trimmedStart);
        }
      }
      rxLen = 0;
    } else if (c != '\r') {
      if (rxLen < RX_BUFFER_SIZE - 1) {
        rxBuffer[rxLen++] = c;
      } else {
        rxOverflow = true;
      }
    }
  }
}

void parseIncomingConfig(char* line) {
  char* modeCountPtr = strstr(line, "\"modeCount\":");
  if (modeCountPtr) {
    int mc = atoi(modeCountPtr + 12);
    if (mc >= 1 && mc <= MAX_MODES) {
      hdr.modeCount = mc;
    }
  }

  // The keys in this line belong to "mode": load that mode first so keys
  // that are not part of this line keep their saved values.
  char* modePtr = strstr(line, "\"mode\":");
  if (modePtr) {
    selectMode(atoi(modePtr + 7));
  } else if (hdr.currentMode >= activeModeCount()) {
    selectMode(0);
  }

  char idNeedle[10];
  for (int i = 0; i < 9; i++) {
    snprintf(idNeedle, sizeof(idNeedle), "\"i\":%d", i);
    char* startPtr = strstr(line, idNeedle);
    if (!startPtr) {
      snprintf(idNeedle, sizeof(idNeedle), "\"i\": %d", i);
      startPtr = strstr(line, idNeedle);
    }

    if (startPtr) {
      char* singlePtr = strstr(startPtr, "\"s\":");
      char* longPtr = strstr(startPtr, "\"l\":");
      char* endBlockPtr = strstr(longPtr ? longPtr : startPtr, "}");

      if (singlePtr && longPtr) {
        parseActionString(singlePtr, longPtr, keys[i].singleClick);
      }
      if (longPtr && endBlockPtr && endBlockPtr > longPtr) {
        parseActionString(longPtr, endBlockPtr, keys[i].longPress);
      }
    }
  }
}

// Commands are matched on the full "cmd":"NAME" pair, not on the bare
// "NAME" string: a key's text macro travels inside SET_CONFIG as "v":"...",
// so a macro whose text was exactly PING used to turn the whole SET_CONFIG
// line into a PING - the device answered like a ping and never saved.
// The GUI always sends compact JSON ({"cmd":"X",...}).
void processSerialLine(char* line) {
  if (strstr(line, "\"cmd\":\"PING\"")) {
#if HAS_HID
    Serial.println(F("{\"status\":\"OK\",\"device\":\"MacroPad3x3\",\"ver\":\"1.1\",\"hid\":1}"));
#else
    Serial.println(F("{\"status\":\"OK\",\"device\":\"MacroPad3x3\",\"ver\":\"1.1\",\"hid\":0}"));
#endif
  }
  else if (strstr(line, "\"cmd\":\"GET_CONFIG\"")) {
    sendFullConfigSerial();
  }
  else if (strstr(line, "\"cmd\":\"SET_MODE\"")) {
    char* mPtr = strstr(line, "\"mode\":");
    if (mPtr) {
      selectMode(atoi(mPtr + 7));
    }
    Serial.println(F("{\"status\":\"SUCCESS\",\"message\":\"Mode Updated\"}"));
  }
  else if (strstr(line, "\"cmd\":\"SET_CONFIG\"") || strstr(line, "\"cmd\":\"SET_KEY\"")) {
    parseIncomingConfig(line);
    saveConfig();
    Serial.println(F("{\"status\":\"SUCCESS\",\"message\":\"Config Saved to EEPROM\"}"));
    Serial.flush();
  }
  else if (strstr(line, "\"cmd\":\"SAVE\"")) {
    saveConfig();
    Serial.println(F("{\"status\":\"SUCCESS\",\"message\":\"Config Saved to EEPROM\"}"));
  }
}
