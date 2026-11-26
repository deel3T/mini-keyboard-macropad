"""Seri hat (kart) için kısa alan adlı tel formatı. main.py'den davranış değişmeden ayrıldı."""

# Wire-format translation for the serial protocol only. The ATmega32U4's
# ~996-byte DeviceConfig global already eats 39% of its 2560-byte SRAM, so
# the firmware's SET_CONFIG receive buffer has to be small and fixed-size
# (no more heap-allocated String - see mini_keyboard.ino's RX_BUFFER_SIZE
# comment). With the old verbose field names ("single", "type", "text"...
# repeated up to 18x per 9-key line) even an all-empty config needed ~570
# bytes, leaving too little RAM for the stack. Shortening the wire format
# (i/s/l/t/m/k/c/v field names, single-char type codes) cuts a typical line
# from ~700 to ~470 bytes. This only affects what goes over the serial
# port - self.modes / user_config.json / the rest of the UI keep using the
# full field names ("type", "single", "mods", ...) exactly as before.
# Firmware'deki tus -> Arduino pin eslemesi (mini_keyboard.ino, KEY_PINS).
# Baslikta "key_id + 2" yaziliyordu; kablolama sirali olmadigi icin yanlisti.
KEY_PINS = (7, 8, 9, 4, 5, 6, 10, 2, 3)
ACTION_TYPE_TO_WIRE = {"none": "n", "shortcut": "s", "text": "x", "media": "d", "mode_switch": "w", "app": "a"}
WIRE_TO_ACTION_TYPE = {v: k for k, v in ACTION_TYPE_TO_WIRE.items()}


# Firmware'in bir eylem basina sakladigi metin (MACRO_TEXT_LEN 8 - 1).
DEVICE_TEXT_LEN = 7


def _device_text(text):
    """Metnin karta gidecek hali: en fazla 7 yazdirilabilir ASCII karakter.

    Kart metni yalnizca bilgisayar bagli degilken kendisi yazar ve Arduino
    Keyboard kutuphanesi ASCII disini bilmez. Tamamini gondermek hem hicbir
    ise yaramiyordu hem de satiri sisiriyordu: json.dumps "s" harfini
    6 karakterlik kacisa cevirir, tirnak ve ters bolu ise firmware'in
    basit ayristiricisini sasirtir.
    """
    out = "".join(ch for ch in (text or "") if 32 <= ord(ch) < 127 and ch not in '"\\')
    return out[:DEVICE_TEXT_LEN]


def compress_action_for_wire(action):
    a_type = (action or {}).get("type", "none")
    wire_type = ACTION_TYPE_TO_WIRE.get(a_type, "n")
    out = {"t": wire_type}
    if a_type == "shortcut":
        out["m"] = action.get("mods", 0) & 0x0F
        out["k"] = action.get("key", 0) & 0xFF
    elif a_type == "text":
        out["v"] = _device_text(action.get("text", ""))
    elif a_type == "media":
        out["c"] = action.get("code", 0)
    # "app": uygulama yolunu kart kullanmaz (programi yalnizca bilgisayar
    # baslatabilir); eskiden tam yol gonderiliyordu ve birkac uygulama
    # tusu satiri kartin 512 baytlik tamponundan tasirip TUM ayari
    # reddettiriyordu - arayuz ise yine "kaydedildi" diyordu.
    return out


def expand_action_from_wire(wire_action):
    wire_type = (wire_action or {}).get("t", "n")
    a_type = WIRE_TO_ACTION_TYPE.get(wire_type, "none")
    out = {"type": a_type}
    if a_type == "shortcut":
        out["mods"] = wire_action.get("m", 0)
        out["key"] = wire_action.get("k", 0)
    elif a_type == "text":
        out["text"] = wire_action.get("v", "")
    elif a_type == "media":
        out["code"] = wire_action.get("c", 0)
    elif a_type == "app":
        out["path"] = wire_action.get("v", "")
    return out


def compress_keys_for_wire(keys):
    return [
        {"i": kdata.get("id", idx), "s": compress_action_for_wire(kdata.get("single")), "l": compress_action_for_wire(kdata.get("long"))}
        for idx, kdata in enumerate(keys)
    ]


def expand_keys_from_wire(wire_keys):
    return [
        {"id": kdata.get("i", idx), "single": expand_action_from_wire(kdata.get("s")), "long": expand_action_from_wire(kdata.get("l"))}
        for idx, kdata in enumerate(wire_keys)
    ]
