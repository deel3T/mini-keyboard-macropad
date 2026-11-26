"""Tuş simülasyonu: Win32 SendInput/keybd_event, panoya yazma, metin makrosu,
sistem medya tuşu ve yakalanan Qt tuşunun VK koduna çevrilmesi.
main.py'den davranış değişmeden ayrıldı."""
import sys
import time
import ctypes
from ctypes import wintypes

from PyQt6.QtCore import Qt

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

# Maps Qt key constants for non-printable keys (arrows, F-keys, navigation)
# to (Windows VK code, short display name). Printable keys (letters/digits)
# don't need an entry here - their VK code is just ord(uppercase char).
QT_SPECIAL_KEY_TO_VK = {
    Qt.Key.Key_Up: (0x26, "↑"),
    Qt.Key.Key_Down: (0x28, "↓"),
    Qt.Key.Key_Left: (0x25, "←"),
    Qt.Key.Key_Right: (0x27, "→"),
    Qt.Key.Key_Escape: (0x1B, "Esc"),
    Qt.Key.Key_Tab: (0x09, "Tab"),
    Qt.Key.Key_Space: (0x20, "Space"),
    Qt.Key.Key_Return: (0x0D, "Enter"),
    Qt.Key.Key_Enter: (0x0D, "Enter"),
    Qt.Key.Key_Backspace: (0x08, "Backspace"),
    Qt.Key.Key_Delete: (0x2E, "Delete"),
    Qt.Key.Key_Home: (0x24, "Home"),
    Qt.Key.Key_End: (0x23, "End"),
    Qt.Key.Key_PageUp: (0x21, "Page Up"),
    Qt.Key.Key_PageDown: (0x22, "Page Down"),
    Qt.Key.Key_F1: (0x70, "F1"), Qt.Key.Key_F2: (0x71, "F2"),
    Qt.Key.Key_F3: (0x72, "F3"), Qt.Key.Key_F4: (0x73, "F4"),
    Qt.Key.Key_F5: (0x74, "F5"), Qt.Key.Key_F6: (0x75, "F6"),
    Qt.Key.Key_F7: (0x76, "F7"), Qt.Key.Key_F8: (0x77, "F8"),
    Qt.Key.Key_F9: (0x78, "F9"), Qt.Key.Key_F10: (0x79, "F10"),
    Qt.Key.Key_F11: (0x7A, "F11"), Qt.Key.Key_F12: (0x7B, "F12"),
}
VK_TO_DISPLAY_NAME = {vk: name for vk, name in QT_SPECIAL_KEY_TO_VK.values()}


# Tek basina basilan degistirici tuslar kisayolun "tusu" sayilmaz: Ctrl'e
# basip ardindan harfe basan kullanicinin yakaladigi tus harf olmali.
_MODIFIER_QT_KEYS = {
    Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr, Qt.Key.Key_Super_L, Qt.Key.Key_Super_R, Qt.Key.Key_CapsLock,
}

# Set-1 tarama kodu (fiziksel tus konumu) -> ABD klavyesinde o tusun VK kodu.
# Firmware tuslari fiziksel konuma gore basar: harf/rakam VK'sini ve Windows
# OEM VK kodlarini ayni fiziksel tusun US karakterine cevirir. Tus yakalanirken
# de fiziksel tus esas alinmali; karakter esas alindiginda Turkce-Q'da "."
# tusu (US'de "/") yanlis tusa, Ctrl basiliyken yakalanan harf ise hicbir
# seye denk gelmiyordu (Ctrl+C'nin metni kontrol karakteridir).
_SCAN_TO_US_VK = {
    0x02: 0x31, 0x03: 0x32, 0x04: 0x33, 0x05: 0x34, 0x06: 0x35,
    0x07: 0x36, 0x08: 0x37, 0x09: 0x38, 0x0A: 0x39, 0x0B: 0x30,
    0x0C: 0xBD, 0x0D: 0xBB,                                   # - =
    0x10: 0x51, 0x11: 0x57, 0x12: 0x45, 0x13: 0x52, 0x14: 0x54,
    0x15: 0x59, 0x16: 0x55, 0x17: 0x49, 0x18: 0x4F, 0x19: 0x50,
    0x1A: 0xDB, 0x1B: 0xDD,                                   # [ ]
    0x1E: 0x41, 0x1F: 0x53, 0x20: 0x44, 0x21: 0x46, 0x22: 0x47,
    0x23: 0x48, 0x24: 0x4A, 0x25: 0x4B, 0x26: 0x4C,
    0x27: 0xBA, 0x28: 0xDE, 0x29: 0xC0, 0x2B: 0xDC,           # ; ' ` \
    0x2C: 0x5A, 0x2D: 0x58, 0x2E: 0x43, 0x2F: 0x56, 0x30: 0x42,
    0x31: 0x4E, 0x32: 0x4D,
    0x33: 0xBC, 0x34: 0xBE, 0x35: 0xBF,                       # , . /
    0x56: 0xE2,                                               # ISO <> tusu
}
_US_VK_TO_SCAN = {vk: sc for sc, vk in _SCAN_TO_US_VK.items()}
_OEM_VKS = {0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF, 0xC0, 0xDB, 0xDC, 0xDD, 0xDE, 0xE2}

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong if sys.maxsize > 2**32 else ctypes.c_ulong)
    ]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong if sys.maxsize > 2**32 else ctypes.c_ulong)
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD)
    ]

class INPUT(ctypes.Structure):
    # The union MUST list all three of Windows' real tagINPUT members
    # (ki/mi/hi), not just the one(s) actually used - ctypes computes the
    # union's size as the size of its *largest* member, and mi is bigger
    # than ki. With only ki declared, ctypes.sizeof(INPUT) comes out
    # smaller than Windows' real INPUT struct (32 bytes vs the true 40 on
    # x64). SendInput compares the cbSize we pass against its own real
    # struct size and rejects the call outright (ERROR_INVALID_PARAMETER)
    # on any mismatch - so every media-key send silently failed until
    # this matched the true size.
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]
    _anonymous_ = ("_u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_u", _U)
    ]

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Without explicit argtypes/restype, ctypes assumes every Win32 call
# returns a 32-bit c_int. GlobalAlloc/GlobalLock/SetClipboardData actually
# return 64-bit handles/pointers on 64-bit Windows - left undeclared, a
# handle above the 32-bit range gets silently truncated, and every later
# call using that truncated "handle" (GlobalLock, memmove, GlobalUnlock,
# SetClipboardData) reads/writes through a garbage pointer. That's a heap
# corruption bug (crashes reported as ntdll.dll / 0xc0000374), triggered
# every time a text macro needs the clipboard fallback (any character
# VkKeyScanW can't map directly, e.g. ç ğ ı ö ş ü). Declaring the real
# pointer-sized types fixes the marshalling so nothing gets truncated.
kernel32.GlobalAlloc.restype = wintypes.HANDLE
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
kernel32.GlobalFree.restype = wintypes.HANDLE
kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
user32.OpenClipboard.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.EmptyClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.CloseClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.SendInput.restype = wintypes.UINT
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]

VK_MEDIA_MAP = {
    1: 0xAD,  # VK_VOLUME_MUTE
    2: 0xAF,  # VK_VOLUME_UP
    3: 0xAE,  # VK_VOLUME_DOWN
    4: 0xB3,  # VK_MEDIA_PLAY_PAUSE
    5: 0xB0,  # VK_MEDIA_NEXT_TRACK
    6: 0xB1,  # VK_MEDIA_PREV_TRACK
}

def qt_key_event_to_vk(qt_key, text, native_scan_code=0):
    """Yakalanan tus olayini (vk_kodu, gorunen_ad) ciftine cevirir.
    Harf/rakam/noktalama icin karakter degil FIZIKSEL tus esas alinir
    (nativeScanCode, Windows'ta set-1 tarama kodu). Tus gonderilirken de
    fiziksel tus kullanildigi icin iki yon ayni tusu gosterir.
    """
    if qt_key in _MODIFIER_QT_KEYS:
        return (0, "")
    if qt_key in QT_SPECIAL_KEY_TO_VK:
        return QT_SPECIAL_KEY_TO_VK[qt_key]
    vk = _SCAN_TO_US_VK.get(native_scan_code)
    if vk:
        return (vk, vk_to_display_name(vk))
    if text and len(text) == 1 and text.isascii() and text.isalnum():
        ch = text.upper()
        return (ord(ch), ch)
    return (0, "")


def _layout_char_for_scan(scan_code):
    """Etkin klavye duzeninde bu fiziksel tusun urettigi karakter."""
    try:
        u32 = ctypes.windll.user32
        local_vk = u32.MapVirtualKeyW(scan_code, 1)      # MAPVK_VSC_TO_VK
        ch = u32.MapVirtualKeyW(local_vk, 2) & 0x7FFF   # MAPVK_VK_TO_CHAR (ust bit: olu tus)
        return chr(ch) if ch else ""
    except Exception:
        return ""


def vk_to_display_name(vk_code):
    if not vk_code:
        return ""
    if vk_code in VK_TO_DISPLAY_NAME:
        return VK_TO_DISPLAY_NAME[vk_code]
    if vk_code in _OEM_VKS:
        ch = _layout_char_for_scan(_US_VK_TO_SCAN.get(vk_code, 0))
        if ch:
            return "İ" if ch == "i" else (ch.upper() if ch.isalpha() else ch)
    if 32 < vk_code < 127:
        return chr(vk_code)
    return f"VK {vk_code}"

def copy_to_windows_clipboard(text):
    if not text:
        return False
    try:
        if not user32.OpenClipboard(None):
            return False
        user32.EmptyClipboard()
        
        encoded = text.encode('utf-16-le') + b'\x00\x00'
        buffer_size = len(encoded)
        
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, buffer_size)
        if not h_mem:
            user32.CloseClipboard()
            return False
            
        p_mem = kernel32.GlobalLock(h_mem)
        if not p_mem:
            kernel32.GlobalFree(h_mem)
            user32.CloseClipboard()
            return False
            
        ctypes.memmove(p_mem, encoded, buffer_size)
        kernel32.GlobalUnlock(h_mem)
        
        user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        user32.CloseClipboard()
        return True
    except Exception:
        try:
            user32.CloseClipboard()
        except Exception:
            pass
        return False

def trigger_shortcut(mods, key_code):
    if not key_code:
        return
    VK_CONTROL = 0x11
    VK_SHIFT = 0x10
    VK_MENU = 0x12
    VK_LWIN = 0x5B

    if mods & 1: user32.keybd_event(VK_CONTROL, 0, 0, 0)
    if mods & 2: user32.keybd_event(VK_SHIFT, 0, 0, 0)
    if mods & 4: user32.keybd_event(VK_MENU, 0, 0, 0)
    if mods & 8: user32.keybd_event(VK_LWIN, 0, 0, 0)

    vk = key_code
    if 97 <= vk <= 122:
        vk -= 32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)

    if mods & 8: user32.keybd_event(VK_LWIN, 0, 2, 0)
    if mods & 4: user32.keybd_event(VK_MENU, 0, 2, 0)
    if mods & 2: user32.keybd_event(VK_SHIFT, 0, 2, 0)
    if mods & 1: user32.keybd_event(VK_CONTROL, 0, 2, 0)

def type_text_as_keystrokes(text):
    if not text:
        return
    VK_SHIFT = 0x10
    for c in text:
        res = user32.VkKeyScanW(ord(c))
        if res == -1:
            copy_to_windows_clipboard(c)
            user32.keybd_event(0x11, 0, 0, 0)
            user32.keybd_event(0x56, 0, 0, 0)
            user32.keybd_event(0x56, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(0x11, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.01)
            continue
        vk = res & 0xFF
        shift = (res >> 8) & 1
        
        if shift: user32.keybd_event(VK_SHIFT, 0, 0, 0)
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.005)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        if shift: user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.005)

def paste_text_from_clipboard(text):
    if not text:
        return
    copy_to_windows_clipboard(text)
    time.sleep(0.04)

    VK_CONTROL = 0x11
    VK_V = 0x56

    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(0x10, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)

    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

def trigger_text(text, mode="type"):
    if not text:
        return
    if mode == "paste":
        paste_text_from_clipboard(text)
    else:
        type_text_as_keystrokes(text)

def send_system_media_key(code):
    """Sistem geneli medya tusu: Windows komutu o an etkin medya oturumuna
    (Spotify, VLC, calan bir tarayici sekmesi...) yonlendirir - fiziksel bir
    multimedya klavyesiyle ayni yol.

    SendInput KEYEVENTF_EXTENDEDKEY ile gonderilir. Medya tuslari "extended"
    tuslardir (gercek tarama kodlarinda 0xE0 oneki var); bayraksiz
    gonderilince odaktaki pencere olayi extended OLMAYAN tabloya gore cozup
    bambaska bir tusa cevirebiliyordu (bu makinede Oynat/Durdur duz bir harf
    tusu olarak inip Masaustu'nde o harfle baslayan simgeyi seciyordu)."""
    vk = VK_MEDIA_MAP.get(code)
    if not vk:
        return
    inputs = (INPUT * 2)()
    for inp, flags in ((inputs[0], KEYEVENTF_EXTENDEDKEY), (inputs[1], KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)):
        inp.type = INPUT_KEYBOARD
        inp.ki.wVk = vk
        inp.ki.wScan = 0
        inp.ki.dwFlags = flags
        inp.ki.time = 0
        inp.ki.dwExtraInfo = 0
    user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
