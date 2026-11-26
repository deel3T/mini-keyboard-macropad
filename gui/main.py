import sys
import json
import os
import re
import time
import socket
import threading
import logging
import logging.handlers
import traceback

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
try:
    os.chdir(project_root)
except Exception:
    pass
if project_root not in sys.path:
    sys.path.insert(0, project_root)
gui_dir = os.path.dirname(__file__)
if gui_dir not in sys.path:
    sys.path.insert(0, gui_dir)

# Konsolsuz pythonw.exe altinda
# calisirken bir Python hatasi olursa stderr hic gorunmuyordu, hata izsiz
# kayboluyordu. Artik hem calisma gunlugu hem de yakalanamayan hatalar
# icin ayri bir crash_log.txt dosyaya yaziliyor.
_log_dir = project_root
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    # Program surekli arka planda calistigi icin gunluk sinirsiz buyumesin:
    # 512 KB'ta bir dondurulur, en fazla iki eski kopya tutulur.
    handlers=[logging.handlers.RotatingFileHandler(
        os.path.join(_log_dir, "minikeyboard_debug.log"),
        maxBytes=512 * 1024, backupCount=2, encoding="utf-8", delay=True)],
)
_logger = logging.getLogger("MiniKeyboard")


def _log_uncaught_exceptions(exctype, value, tb):
    err_msg = "".join(traceback.format_exception(exctype, value, tb))
    _logger.error(f"UNCAUGHT EXCEPTION:\n{err_msg}")
    try:
        with open(os.path.join(_log_dir, "crash_log.txt"), "a", encoding="utf-8") as f:
            f.write(f"\n--- Mini Keyboard CRASH LOG [{time.strftime('%Y-%m-%d %H:%M:%S')}] ---\n")
            f.write(err_msg)
            f.write("\n" + "=" * 50 + "\n")
    except Exception:
        pass
    sys.__excepthook__(exctype, value, tb)


sys.excepthook = _log_uncaught_exceptions


def _log_uncaught_thread_exceptions(args):
    _log_uncaught_exceptions(args.exc_type, args.exc_value, args.exc_traceback)


threading.excepthook = _log_uncaught_thread_exceptions
_logger.info("Mini Keyboard baslatildi (pid=%s).", os.getpid())

APP_VERSION = "v20.9.0"
import ctypes
import subprocess
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QComboBox, QLineEdit, QCheckBox,
    QGroupBox, QTabWidget, QFileDialog, QMessageBox, QFrame, QProgressDialog,
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QCompleter, QDoubleSpinBox,
    QSystemTrayIcon, QMenu, QRadioButton, QStackedWidget,
    QScrollArea, QSizePolicy, QFileIconProvider
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QRectF, pyqtSignal, pyqtProperty, QSize, QFileInfo
from PyQt6.QtGui import QFont, QColor, QGuiApplication, QPainter, QAction, QIcon, QPixmap, QPen, QBrush, QLinearGradient

from serial_handler import SerialWorker, DISCONNECTED
from i18n import t as tr_en, set_language
from arduino_flasher import ArduinoFlasherWorker

# Davranış değişmeden ayrılan modüller (eskiden hepsi bu dosyadaydı).
from key_input import (
    qt_key_event_to_vk, vk_to_display_name, trigger_shortcut, trigger_text,
)
from wire_format import KEY_PINS, compress_keys_for_wire
from action_queue import run_in_background, shutdown_actions
from media import (
    MEDIA_ITEMS_TR, MEDIA_ITEMS_EN, MEDIA_GLYPHS, PLAYER_TARGETABLE_CODES,
    MUSIC_ROLE, VIDEO_ROLE, BROWSER_PRESETS, set_role_targets, role_target,
    _smtc_sunucu, player_cache, list_players, player_display_name,
    media_action_target, smtc_available, trigger_media,
)
from app_launcher import (
    fix_drive_relative_path, get_installed_windows_apps, get_app_name_for_path,
    scheduled_task_name_for_app, scheduled_task_exists, run_scheduled_task,
    create_admin_launch_task, delete_admin_launch_task, find_running_app_window,
    activate_window, kisayollari_onceden_coz,
)





def blend_hex(base_hex, accent_hex, ratio):
    """Linearly blends accent_hex into base_hex by ratio (0-1). Used to tint
    a panel/pill background with a mode's own color, the way the approved
    mockup used CSS color-mix() - Qt's QSS has no equivalent function."""
    base = QColor(base_hex)
    accent = QColor(accent_hex)
    r = round(base.red() * (1 - ratio) + accent.red() * ratio)
    g = round(base.green() * (1 - ratio) + accent.green() * ratio)
    b = round(base.blue() * (1 - ratio) + accent.blue() * ratio)
    return f"#{r:02X}{g:02X}{b:02X}"



def create_app_icon():
    """3x3 macro keypad glyph: a metallic chassis holding nine keycaps with
    real per-key depth (top-lit gradient + bottom shadow ridge), and the
    center key lit up like an active macro trigger."""
    size = 128
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    macro_accent = QColor("#F2A96C")

    # Outer chassis
    chassis_rect = QRectF(4, 4, 120, 120)
    chassis_grad = QLinearGradient(0, 0, 0, size)
    chassis_grad.setColorAt(0.0, QColor("#1B241F"))
    chassis_grad.setColorAt(1.0, QColor("#0A0F0C"))
    painter.setBrush(QBrush(chassis_grad))
    painter.setPen(QPen(QColor("#3A4C40"), 2))
    painter.drawRoundedRect(chassis_rect, 26, 26)

    # Inner plate the keycaps sit on
    plate_rect = QRectF(11, 11, 106, 106)
    painter.setBrush(QBrush(QColor("#0E1411")))
    painter.setPen(QPen(QColor("#24322A"), 1.5))
    painter.drawRoundedRect(plate_rect, 18, 18)

    key_size, gap, start = 25, 5, 17
    for row in range(3):
        for col in range(3):
            k_x = start + col * (key_size + gap)
            k_y = start + row * (key_size + gap)
            k_rect = QRectF(k_x, k_y, key_size, key_size)
            is_center = row == 1 and col == 1

            cap_grad = QLinearGradient(k_x, k_y, k_x, k_y + key_size)
            if is_center:
                cap_grad.setColorAt(0.0, QColor("#241A12"))
                cap_grad.setColorAt(1.0, QColor("#120D08"))
            else:
                cap_grad.setColorAt(0.0, QColor("#1B241F"))
                cap_grad.setColorAt(1.0, QColor("#0F1512"))
            painter.setBrush(QBrush(cap_grad))
            painter.setPen(QPen(macro_accent if is_center else QColor("#3A4C40"), 1.4))
            painter.drawRoundedRect(k_rect, 7, 7)

            # Bottom ridge - gives each keycap a pressed-in depth cue
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(0, 0, 0, 90)))
            painter.drawRoundedRect(QRectF(k_x + 2, k_y + key_size - 6, key_size - 4, 5), 3, 3)

            if is_center:
                glow = QColor(macro_accent)
                glow.setAlpha(170)
                painter.setBrush(QBrush(glow))
                painter.setPen(Qt.PenStyle.NoPen)
                r = 5
                painter.drawEllipse(QRectF(k_x + key_size / 2 - r, k_y + key_size / 2 - r, r * 2, r * 2))

    painter.end()
    return QIcon(pixmap)

def save_and_update_desktop_shortcut():
    try:
        project_assets = os.path.join(project_root, "assets")
        os.makedirs(project_assets, exist_ok=True)
        ico_path = os.path.join(project_assets, "app_icon.ico")
        png_path = os.path.join(project_assets, "app_icon.png")

        icon = create_app_icon()
        pixmap = icon.pixmap(128, 128)
        pixmap.save(png_path, "PNG")
        pixmap.save(ico_path, "ICO")

        script_path = os.path.abspath(sys.argv[0])
        # MiniKeyboardConfigurator.exe: pythonw.exe'nin adli kopyasi (Gorev Yoneticisi'nde "MiniKB")
        _exe_dir = os.path.dirname(sys.executable)
        pythonw_path = os.path.join(_exe_dir, "MiniKeyboardConfigurator.exe")
        if not os.path.exists(pythonw_path):
            pythonw_path = os.path.join(_exe_dir, "pythonw.exe")

        ps_cmd = f"""
        $WshShell = New-Object -ComObject WScript.Shell
        $DesktopPath = [System.Environment]::GetFolderPath('Desktop')

        $DuplicateLnk = Join-Path $DesktopPath "Mini Keyboard.lnk"
        if (Test-Path $DuplicateLnk) {{
            Remove-Item -Force $DuplicateLnk
        }}

        $TargetLnk = Join-Path $DesktopPath "Mini Keyboard Configurator.lnk"
        $Shortcut = $WshShell.CreateShortcut($TargetLnk)
        $Shortcut.TargetPath = "{pythonw_path}"
        $Shortcut.Arguments = "`"{script_path}`" --show"
        $Shortcut.WorkingDirectory = "{project_root}"
        $Shortcut.IconLocation = "{ico_path}"
        $Shortcut.Save()
        """
        # --show SART: program acilista tepside calisiyor; ikinci kopya (kisayola
        # tiklama) mutex'i gorup cikarken calisan kopyaya "pencereyi ac" sinyalini
        # yalnizca --show varsa gonderir. Eskiden kisayol --show'suz yaziliyordu ve
        # tiklamak hicbir sey yapmiyordu (08.10'da masaustu kisayolunda dogrulandi).
        # PowerShell (~0,3 sn) arayuz thread'ini bekletmesin diye ayri thread'de.
        def _yaz():
            try:
                subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True,
                               text=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
            except (OSError, subprocess.SubprocessError) as exc:
                _logger.warning("Masaustu kisayolu yazilamadi: %s", exc)
                return
            try:
                ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
            except Exception:
                pass
        threading.Thread(target=_yaz, name="MiniKB-Kisayol", daemon=True).start()
    except Exception:
        pass

PRESET_COLORS = [
    {"icon": "🟠", "name_tr": "🟠 Bakır", "name_en": "🟠 Copper", "hex": "#D98A54"},
    {"icon": "🔴", "name_tr": "🔴 Kırmızı", "name_en": "🔴 Red", "hex": "#FF6B6B"},
    {"icon": "🟢", "name_tr": "🟢 Sinyal Yeşili", "name_en": "🟢 Signal Green", "hex": "#8CFFC7"},
    {"icon": "🔵", "name_tr": "🔵 Mavi", "name_en": "🔵 Blue", "hex": "#4FB8E8"},
    {"icon": "🟣", "name_tr": "🟣 Mor", "name_en": "🟣 Purple", "hex": "#B57BF2"},
    {"icon": "🟡", "name_tr": "🟡 Amber", "name_en": "🟡 Amber", "hex": "#FFB454"},
    {"icon": "⚪", "name_tr": "⚪ Beyaz", "name_en": "⚪ White", "hex": "#EDEAE0"}
]

_FLASH_RULE_RE = re.compile(r'QPushButton#gridKey\[flashing="true"\]\s*\{[^}]*\}', re.DOTALL)

ACTION_TYPE_COLORS = {
    "shortcut": "#8CFFC7",
    "text": "#F2A96C",
    "media": "#FFB454",
    "mode_switch": "#B57BF2",
    "app": "#7CCEF0",
}


TRANSLATIONS = {
    "TR": {
        "nav_keyboard": "Tuş Yapılandırıcı",
        "nav_settings": "⚙",
        "com_port": "COM Port:",
        "refresh_tooltip": "Portları Yenile",
        "connect": "Bağlan",
        "disconnect": "Bağlantıyı Kes",
        "connecting": "{} Bağlanıyor...",
        "connected": "{} Bağlı",
        "disconnected": "Bağlı Değil",
        "mod_prefix": "MOD",
        "phys_keyboard": "Fiziksel Klavye (1 ➔ 9)",
        "tab_single": "Tek Tıklama",
        "tab_long": "Uzun Basma",
        "save_upload": "⚡ Kaydet && Yükle",
        "key_settings_title": "{} ➔ {} Ayarları (Arduino Pin {})",
        "select_key_prompt": "{} ➔ Lütfen Bir Tuş Seçin",
        "lang_group": "Dil",
        "mode_count_group": "Mod Sayısı",
        "port_group": "Kart ve Varsayılan Port",
        "board_hint_promicro": "Pro Micro / Micro: kendi başına USB klavyedir. Program kapalıyken de kısayol ve kısa metinler çalışır.",
        "board_hint_nano": "Nano: USB klavye olamaz (USB'yi ayrı bir köprü yongası sağlar). Tuşlar yalnızca bu program açıkken çalışır. Port açılınca kart yeniden başladığı için Nano'da otomatik arama yapılmaz: portu aşağıdan seç.",
        "nano_pick_port": "Nano için portu Ayarlar'dan ya da üst çubuktan seç",
        "auto_detect": "Otomatik",
        "mode_colors_group": "Mod İsimleri & Renkleri",
        "mode_name_placeholder": "MOD {}",
        "gen_group": "Genel",
        "autostart": "Başlangıçta Windows ile aç",
        "tray_close": "Kapatınca sistem tepsisine küçült",
        "popup_notify": "Mod değişince bildirim göster",
        "browser_group": "Medya Tarayıcıları",
        "browser_hint": "Tuşlarda “Müzik tarayıcısı” ya da “Video tarayıcısı” hedefini seçersen komut buradaki uygulamaya gider. Seçimi değiştirince o tuşların hepsi kendiliğinden yeni tarayıcıya geçer.",
        "browser_music": "🎵 Müzik",
        "browser_video": "🎬 Video",
        "browser_none": "Seçilmedi (sistem geneli)",
        "browser_open": "açık",
        "browser_refresh_tip": "Açık oynatıcıları yenile",
        "profile_group": "Profil",
        "firmware_group": "Kart Yazılımı (Firmware)",
        "firmware_hint": "arduino/mini_keyboard içindeki kodu seçili kart türüne göre derleyip karta yükler. Tuş ayarları EEPROM'da kalır.",
        "firmware_btn": "⬆ Firmware'i Karta Yükle",
        "firmware_confirm": "Firmware derlenip karta yüklenecek. Yükleme sırasında (~20 sn) klavye çalışmaz. Devam edilsin mi?",
        "export_profile": "Dışa Aktar (.json)",
        "import_profile": "İçe Aktar",
        "save_settings": "Ayarları Kaydet",
        "settings_saved": "Program ayarları başarıyla kaydedildi!",
        "save_success_title": "Kaydedildi",
        "save_success_msg": "{} Ayarlarınız Başarıyla Kalıcı Olarak Kaydedildi!"
    },
    "EN": {
        "nav_keyboard": "Key Configurator",
        "nav_settings": "⚙",
        "com_port": "COM Port:",
        "refresh_tooltip": "Refresh Ports",
        "connect": "Connect",
        "disconnect": "Disconnect",
        "connecting": "Connecting: {}...",
        "connected": "{} Connected",
        "disconnected": "Disconnected",
        "mod_prefix": "MODE",
        "phys_keyboard": "Physical Keypad (1 ➔ 9)",
        "tab_single": "Single Press",
        "tab_long": "Long Press",
        "save_upload": "⚡ Save && Upload",
        "key_settings_title": "{} ➔ Key {} Settings (Arduino Pin {})",
        "select_key_prompt": "{} ➔ Please Select a Key",
        "lang_group": "Language",
        "mode_count_group": "Mode Count",
        "port_group": "Board & Default Port",
        "board_hint_promicro": "Pro Micro / Micro: a USB keyboard on its own. Shortcuts and short text macros work even with this app closed.",
        "board_hint_nano": "Nano: cannot be a USB keyboard (a separate bridge chip provides USB). Keys only work while this app runs. Opening its port resets the board, so there is no auto-search on Nano: pick the port below.",
        "nano_pick_port": "For a Nano, pick its port in Settings or the top bar",
        "auto_detect": "Auto",
        "mode_colors_group": "Mode Names & Colors",
        "mode_name_placeholder": "MODE {}",
        "gen_group": "General",
        "autostart": "Run on Windows Startup",
        "tray_close": "Minimize to tray on close",
        "popup_notify": "Show notification on mode switch",
        "browser_group": "Media Browsers",
        "browser_hint": "Keys targeting “Music browser” or “Video browser” send their command to the app chosen here. Change it and every such key follows the new browser.",
        "browser_music": "🎵 Music",
        "browser_video": "🎬 Video",
        "browser_none": "Not set (system-wide)",
        "browser_open": "open",
        "browser_refresh_tip": "Refresh open players",
        "profile_group": "Profile",
        "firmware_group": "Board Firmware",
        "firmware_hint": "Compiles arduino/mini_keyboard for the selected board type and uploads it. Key settings stay in EEPROM.",
        "firmware_btn": "⬆ Upload Firmware",
        "firmware_confirm": "The firmware will be compiled and uploaded. The keypad won't work during the upload (~20 s). Continue?",
        "export_profile": "Export (.json)",
        "import_profile": "Import",
        "save_settings": "Save Settings",
        "settings_saved": "Program settings saved successfully!",
        "save_success_title": "Saved",
        "save_success_msg": "{} Settings Saved Permanently!"
    }
}


USER_CONFIG_FILE = os.path.join(project_root, "user_config.json")
# Ilk acilista (user_config.json yokken) baslangic profili; ilk kayitta
# user_config.json olusur, ornek dosya hic degistirilmez.
EXAMPLE_CONFIG_FILE = os.path.join(project_root, "user_config.example.json")



class ModeNotificationPopup(QWidget):
    """ Multi-Monitor Aware Pure Floating Text OSD. """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        main_layout = QHBoxLayout(self)
        # Margin around the label matches the shadow's blur radius below, so
        # the glow has room to fade out smoothly instead of being clipped
        # hard against this frameless window's own edge (which read as
        # jagged "spikes" cut off right at the rounded corners).
        main_layout.setContentsMargins(26, 26, 26, 26)
        main_layout.setSpacing(0)

        self.lbl_msg = QLabel("MOD 1")
        font_msg = QFont("Segoe UI Variable Display", 20, QFont.Weight.Bold)
        font_msg.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.lbl_msg.setFont(font_msg)
        main_layout.addWidget(self.lbl_msg)

        # Ambient Glow Shadow directly on text
        self.shadow = QGraphicsDropShadowEffect(self)
        self.shadow.setBlurRadius(24)
        self.shadow.setOffset(0, 0)
        self.lbl_msg.setGraphicsEffect(self.shadow)

        # Smooth Opacity Fade Animation
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)

        self.anim_fade = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.anim_fade.setDuration(220)

        self.timer_hide = QTimer(self)
        self.timer_hide.setSingleShot(True)
        self.timer_hide.timeout.connect(self.start_fade_out)

    def show_mode(self, mode_idx, mode_color="#D98A54", mode_name=None, target_screen=None):
        try:
            self.anim_fade.finished.disconnect(self.on_fade_out_finished)
        except Exception:
            pass

        self.timer_hide.stop()
        self.anim_fade.stop()

        self.lbl_msg.setText(mode_name if mode_name else f"MOD {mode_idx + 1}")
        self.lbl_msg.setStyleSheet(f"""
            QLabel {{
                color: {mode_color};
                background-color: rgba(10, 15, 12, 0.92);
                border: 1px solid {mode_color};
                border-radius: 12px;
                font-size: 15px;
                font-weight: bold;
                padding: 8px 18px;
            }}
        """)
        self.shadow.setColor(QColor(mode_color))
        self.adjustSize()

        screen = target_screen if target_screen else QGuiApplication.primaryScreen()
        if screen:
            rect = screen.availableGeometry()
            # self.width()/height() now include the 26px glow margin added
            # above, so the on-screen gap is shrunk by that much to keep the
            # visible pill sitting at roughly the same spot as before.
            x = rect.x() + rect.width() - self.width() - 14
            y = rect.y() + rect.height() - self.height() - 24
            self.move(x, y)

        self.opacity_effect.setOpacity(0.0)
        self.show()

        self.anim_fade.setStartValue(0.0)
        self.anim_fade.setEndValue(1.0)
        self.anim_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim_fade.start()

        self.timer_hide.start(1500)

    def start_fade_out(self):
        self.anim_fade.stop()
        try:
            self.anim_fade.finished.disconnect(self.on_fade_out_finished)
        except Exception:
            pass
        self.anim_fade.finished.connect(self.on_fade_out_finished)

        self.anim_fade.setStartValue(self.opacity_effect.opacity())
        self.anim_fade.setEndValue(0.0)
        self.anim_fade.setEasingCurve(QEasingCurve.Type.InCubic)
        self.anim_fade.start()

    def on_fade_out_finished(self):
        try:
            self.anim_fade.finished.disconnect(self.on_fade_out_finished)
        except Exception:
            pass
        self.hide()


DARK_STYLE = """
QMainWindow {
    background-color: #0A0F0C;
}

QWidget {
    color: #EDEAE0;
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
}

QGroupBox {
    border: 1px solid #24322A;
    border-radius: 8px;
    margin-top: 12px;
    font-weight: bold;
    font-family: 'Bahnschrift SemiBold', 'Segoe UI Semibold', 'Segoe UI', Arial;
    color: #D98A54;
    background-color: #131A16;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}

QPushButton {
    background-color: #17201A;
    border: 1px solid #24322A;
    border-radius: 6px;
    padding: 8px 16px;
    color: #EDEAE0;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #24322A;
    border-color: #8B9289;
    color: #EDEAE0;
}

QPushButton:pressed {
    background-color: #131A16;
}

QPushButton:disabled {
    background-color: #131A16;
    color: #525A50;
    border-color: #17201A;
}

QPushButton#gridKey {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1B241F, stop:0.55 #141B17, stop:1 #0F1512);
    border-top: 1px solid #24322A;
    border-left: 1px solid #24322A;
    border-right: 1px solid #24322A;
    border-bottom: 5px solid #060907;
    border-radius: 12px;
    font-size: 14px;
    font-weight: bold;
    color: #8B9289;
    min-width: 88px;
    min-height: 88px;
    max-width: 88px;
    max-height: 88px;
    margin: 0px;
    padding: 0px;
}

QPushButton#gridKey:hover {
    border-top: 1px solid #3A4C40;
    border-left: 1px solid #3A4C40;
    border-right: 1px solid #3A4C40;
    border-bottom: 5px solid #060907;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #212B25, stop:0.55 #19221D, stop:1 #131A16);
    color: #F2A96C;
    margin: 0px;
    padding: 0px;
}

QPushButton#gridKey:pressed {
    border-top: 1px solid #F2A96C;
    border-left: 1px solid #F2A96C;
    border-right: 1px solid #F2A96C;
    border-bottom: 5px solid #060907;
    background-color: #0A0D0B;
    color: #F2A96C;
    margin: 0px;
    padding: 0px;
}

QPushButton#gridKey[pressedAnim="true"] {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #10150F, stop:0.55 #0C100D, stop:1 #080A08);
    border-bottom: 2px solid #060907;
}

QPushButton#gridKey[selected="true"] {
    border-top: 2px solid #D98A54;
    border-left: 2px solid #D98A54;
    border-right: 2px solid #D98A54;
    border-bottom: 5px solid #060907;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #241C15, stop:0.55 #1B1610, stop:1 #14100B);
    color: #F2A96C;
    margin: 0px;
    padding: 0px;
}

QPushButton#gridKey[flashing="true"] {
    border-top: 2px solid MODE_COLOR_PLACEHOLDER;
    border-left: 2px solid MODE_COLOR_PLACEHOLDER;
    border-right: 2px solid MODE_COLOR_PLACEHOLDER;
    border-bottom: 2px solid MODE_COLOR_PLACEHOLDER;
    background-color: #0E1411;
    color: MODE_COLOR_PLACEHOLDER;
    margin: 0px;
    padding: 0px;
}

QLineEdit, QComboBox {
    background-color: #0A0F0C;
    border: 1px solid #24322A;
    border-radius: 6px;
    padding: 6px;
    color: #D98A54;
}

QLineEdit:focus, QComboBox:focus {
    border: 1px solid #D98A54;
}

QComboBox::drop-down {
    border: none;
    width: 22px;
}

QComboBox::down-arrow {
    width: 10px;
    height: 10px;
}

QComboBox QAbstractItemView {
    background-color: #131A16;
    border: 1px solid #24322A;
    border-radius: 6px;
    color: #EDEAE0;
    selection-background-color: #1FAE78;
    selection-color: #EDEAE0;
    outline: none;
    padding: 4px;
}

QRadioButton {
    spacing: 8px;
}

QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #24322A;
    border-radius: 8px;
    background: #0A0F0C;
}

QRadioButton::indicator:hover {
    border-color: #D98A54;
}

QRadioButton::indicator:checked {
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 #D98A54, stop:0.45 #D98A54, stop:0.55 #0A0F0C, stop:1 #0A0F0C);
    border-color: #D98A54;
}

QScrollBar:vertical {
    background: #0A0F0C;
    width: 10px;
    margin: 0px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background: #24322A;
    border-radius: 5px;
    min-height: 24px;
}

QScrollBar::handle:vertical:hover {
    background: #D98A54;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background: #0A0F0C;
    height: 10px;
    margin: 0px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal {
    background: #24322A;
    border-radius: 5px;
    min-width: 24px;
}

QScrollBar::handle:horizontal:hover {
    background: #D98A54;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

QToolTip {
    background-color: #131A16;
    color: #EDEAE0;
    border: 1px solid #24322A;
    padding: 5px 10px;
    border-radius: 4px;
}

QMessageBox, QDialog, QProgressDialog {
    background-color: #0A0F0C;
}

QProgressDialog QLabel {
    color: #EDEAE0;
}

QTabWidget::pane {
    border: none;
    border-top: 1px solid #24322A;
    background-color: transparent;
    top: -1px;
}

QTabBar::tab {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 9px 2px;
    margin-right: 22px;
    color: #525A50;
    font-weight: 600;
}

QTabBar::tab:selected {
    color: #EDEAE0;
    border-bottom: 2px solid #D98A54;
}

QTabBar::tab:hover {
    color: #8B9289;
}

QCheckBox {
    spacing: 8px;
}

QCheckBox::indicator {
    width: 30px;
    height: 16px;
    border: 1px solid #24322A;
    border-radius: 8px;
    background: #0E1411;
}

QCheckBox::indicator:checked {
    background: rgba(140, 255, 199, 40);
    border-color: #8CFFC7;
}

QPushButton#smallIconBtn {
    padding: 0px;
    font-size: 15px;
}

QLabel#fieldLabel {
    font-size: 10.5px;
    letter-spacing: 1px;
    color: #525A50;
    font-weight: 700;
}

QPushButton#modKeyToggle {
    background-color: #0E1411;
    border: 1px solid #24322A;
    border-radius: 6px;
    padding: 8px 4px;
    min-width: 52px;
    font-family: 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    font-size: 11px;
    font-weight: 600;
    color: #8B9289;
}

QPushButton#modKeyToggle:checked {
    border-color: #8CFFC7;
    color: #8CFFC7;
    background-color: rgba(140, 255, 199, 35);
}

QPushButton#chipBtn {
    background-color: #0E1411;
    border: 1px solid #24322A;
    border-radius: 7px;
    padding: 7px 13px;
    font-size: 12px;
    font-weight: 600;
    color: #8B9289;
}

QPushButton#chipBtn:hover {
    border-color: #3A4C40;
    color: #EDEAE0;
}

QPushButton#chipBtn:checked {
    border-color: #D98A54;
    color: #F2A96C;
    background-color: rgba(217, 138, 84, 30);
}

QLineEdit#keyCapture {
    border: 1px dashed #3A4C40;
    background-color: #0E1411;
}

QLineEdit#keyCapture:focus {
    border: 1px dashed #D98A54;
}

QFrame#panelFrame {
    background-color: #131A16;
    border: 1px solid #24322A;
    border-radius: 12px;
}

QLabel#panelTitle {
    font-family: 'Bahnschrift SemiBold', 'Segoe UI Semibold', 'Segoe UI', Arial;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    color: #8B9289;
}

QLabel#panelHint {
    font-family: 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    font-size: 10.5px;
    color: #525A50;
}

QPushButton#modePill {
    border-radius: 17px;
    text-align: left;
}

QFrame#topRail {
    background-color: #131A16;
    border: 1px solid #24322A;
    border-radius: 10px;
}

QLabel#brandTitle {
    font-family: 'Bahnschrift SemiBold', 'Segoe UI Semibold', 'Segoe UI', Arial;
    font-size: 15px;
    font-weight: 700;
    color: #EDEAE0;
}

QLabel#brandSub {
    font-family: 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    font-size: 10px;
    color: #525A50;
}

QPushButton#iconBtn {
    background-color: #0E1411;
    border: 1px solid #24322A;
    border-radius: 8px;
    font-size: 13px;
    color: #8B9289;
    padding: 0px;
}

QPushButton#iconBtn:hover {
    border-color: #D98A54;
    color: #F2A96C;
}

QPushButton#settingsBack {
    background: transparent;
    border: none;
    color: #8B9289;
    font-size: 12.5px;
    font-weight: 600;
    text-align: left;
    padding: 4px 2px;
}

QPushButton#settingsBack:hover {
    color: #F2A96C;
}

QPushButton#iconBtn[active="true"] {
    border-color: #D98A54;
    color: #F2A96C;
    background-color: rgba(217, 138, 84, 25);
}

QFrame#portPill {
    background-color: #0E1411;
    border: 1px solid #24322A;
    border-radius: 16px;
}

QComboBox#portCombo {
    background: transparent;
    border: none;
    padding: 2px 4px;
    font-size: 12px;
    font-weight: 700;
    font-family: 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    color: #D98A54;
}

QComboBox#portCombo::drop-down {
    border: none;
    width: 14px;
}

QPushButton#modeAddBtn {
    background: transparent;
    border: 1px dashed #3A4C40;
    border-radius: 15px;
    color: #525A50;
    font-size: 15px;
    padding: 0px;
}

QPushButton#modeAddBtn:hover {
    border-color: #D98A54;
    color: #F2A96C;
}

QFrame#specCard {
    background-color: #131A16;
    border: 1px solid #24322A;
    border-radius: 12px;
}

QFrame#segmented {
    border: 1px solid #24322A;
    border-radius: 7px;
    background-color: #0E1411;
}

QPushButton#segBtn {
    background: transparent;
    border: none;
    padding: 7px 16px;
    font-size: 12px;
    font-weight: 600;
    color: #8B9289;
}

QPushButton#segBtn:checked {
    background-color: #D98A54;
    color: #20130A;
    border-radius: 6px;
}

QFrame#stepperFrame {
    background-color: #0E1411;
    border: 1px solid #24322A;
    border-radius: 7px;
}

QPushButton#stepperBtn {
    background: transparent;
    border: none;
    color: #F2A96C;
    font-size: 15px;
    font-weight: bold;
    min-width: 18px;
}

QLabel#stepperVal {
    font-family: 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    font-size: 14px;
    color: #EDEAE0;
    min-width: 14px;
}

QPushButton#ghostBtn {
    background-color: #0E1411;
    border: 1px solid #24322A;
    border-radius: 7px;
    padding: 9px 15px;
    font-size: 12px;
    font-weight: 600;
    color: #8B9289;
}

QPushButton#ghostBtn:hover {
    border-color: #D98A54;
    color: #F2A96C;
}

QPushButton#accentBtnSmall {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F2A96C, stop:1 #D98A54);
    color: #20130A;
    font-weight: bold;
    padding: 9px 18px;
    font-size: 12px;
    border: none;
    border-radius: 7px;
}

QPushButton#accentBtnSmall:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FFC084, stop:1 #F2A96C);
}

QComboBox#settingsCombo {
    background-color: #0A0F0C;
    border: 1px solid #24322A;
    border-radius: 6px;
    padding: 7px 10px;
    color: #EDEAE0;
    font-size: 12.5px;
}

QComboBox#settingsCombo:focus {
    border-color: #D98A54;
}

QLineEdit#settingsInput {
    background-color: #0E1411;
    border: 1px solid #24322A;
    border-radius: 6px;
    padding: 6px 10px;
    color: #EDEAE0;
    font-size: 12.5px;
}

QLineEdit#settingsInput:focus {
    border-color: #D98A54;
}
"""


class KeyCaptureLineEdit(QLineEdit):
    """Read-only field that captures the next physical key press (letters,
    digits, arrows, F-keys, Home/End, etc.) instead of accepting typed text -
    so shortcuts aren't limited to single printable characters."""
    keyCaptured = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def mousePressEvent(self, event):
        self.setFocus()
        self.setText(tr_en("Bir tuşa basın...", "Press a key..."))
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        vk, name = qt_key_event_to_vk(event.key(), event.text(), event.nativeScanCode())
        if vk:
            self.keyCaptured.emit(vk, name)
        event.accept()


class ChipSelector(QWidget):
    """Row of exclusive toggle chips that mirrors the subset of QComboBox's
    API this app actually reads/writes (currentIndexChanged, setCurrentIndex,
    currentIndex, setItemText) so it drops in wherever a type-selector combo
    box used to sit, without touching any of the code around it."""
    currentIndexChanged = pyqtSignal(int)

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self._buttons = []
        self._index = -1
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for i, label in enumerate(items):
            btn = QPushButton(label)
            btn.setObjectName("chipBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, i=i: self.setCurrentIndex(i))
            layout.addWidget(btn)
            self._buttons.append(btn)
        layout.addStretch()
        if self._buttons:
            self._buttons[0].setChecked(True)
            self._index = 0

    def setCurrentIndex(self, index):
        if index == self._index:
            return
        self._index = index
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == index)
        self.currentIndexChanged.emit(index)

    def currentIndex(self):
        return self._index

    def setItemText(self, index, text):
        if 0 <= index < len(self._buttons):
            self._buttons[index].setText(text)


ADMIN_TIP_TR = (
    "Bazı programlar yönetici izni ister. Normalde Windows her seferinde\n"
    "onay penceresi (UAC) gösterir. İşaretleyip kaydedince bu program için\n"
    "Görev Zamanlayıcı'da \"MiniKeyboard_<ProgramAdı>\" adlı, en yüksek\n"
    "izinle çalışan bir görev oluşturulur; sonraki açılışlarda UAC çıkmaz."
)
ADMIN_TIP_EN = (
    "Some programs need administrator rights. Normally Windows shows a\n"
    "UAC prompt every time. When checked and saved, a Task Scheduler task\n"
    "named \"MiniKeyboard_<AppName>\" with highest privileges is created\n"
    "for this program, so later launches skip the UAC prompt."
)


class ToggleSwitch(QCheckBox):
    """Kayan yuvarlakli ac/kapa anahtari, istege bagli yaziyla.

    Eski anahtar yalnizca QSS ile cizilmis bir hapti: acik/kapali farki
    sadece renkteydi, yuvarlak yoktu. Yuvarlak 160 ms'de kayar.
    """
    TRACK_W, TRACK_H = 34, 18

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _get_pos(self):
        return self._pos

    def _set_pos(self, value):
        self._pos = value
        self.update()

    knobPos = pyqtProperty(float, _get_pos, _set_pos)

    def _animate(self, on):
        self._anim.stop()
        if not self.isVisible():
            # Gorunmezken (ayar yuklenirken) animasyon yok, dogrudan yerine.
            self._set_pos(1.0 if on else 0.0)
            return
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sizeHint(self):
        fm = self.fontMetrics()
        text_w = (9 + fm.horizontalAdvance(self.text())) if self.text() else 0
        return QSize(self.TRACK_W + 2 + text_w, max(self.TRACK_H + 6, fm.height() + 6))

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.45)
        tw, th = self.TRACK_W, self.TRACK_H
        y = (self.height() - th) / 2
        k = self._pos

        def mix(a, b):
            return QColor(*(round(a[i] + (b[i] - a[i]) * k) for i in range(4)))

        p.setPen(QPen(mix((36, 50, 42, 255), (140, 255, 199, 255)), 1))
        p.setBrush(mix((14, 20, 17, 255), (140, 255, 199, 45)))
        p.drawRoundedRect(QRectF(1, y, tw, th), th / 2, th / 2)
        d = th - 6
        x = 1 + 3 + k * (tw - 6 - d)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix((82, 90, 80, 255), (140, 255, 199, 255)))
        p.drawEllipse(QRectF(x, y + 3, d, d))
        if self.text():
            p.setOpacity(1.0 if self.isEnabled() else 0.45)
            p.setPen(self.palette().color(self.foregroundRole()))
            p.setFont(self.font())
            p.drawText(QRectF(tw + 10, 0, self.width() - tw - 10, self.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


class KeyConfigWidget(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)

        type_layout = QVBoxLayout()
        type_layout.setSpacing(8)
        self.lbl_action_type = QLabel("EYLEM TÜRÜ")
        self.lbl_action_type.setObjectName("fieldLabel")
        type_layout.addWidget(self.lbl_action_type)
        self.combo_type = ChipSelector([
            "Devre Dışı",
            "Kısayol Tuşu",
            "Metin Makrosu",
            "Medya Kontrolü",
            "Mod Değiştirici (Sonraki Mod)",
            "🚀 Program / Oyun Çalıştır"
        ])
        self.combo_type.currentIndexChanged.connect(self.on_type_changed)
        type_layout.addWidget(self.combo_type)
        self.layout.addLayout(type_layout)

        # 1. Shortcut Widget
        self.shortcut_box = QGroupBox("Kısayol Ayarları")
        sc_layout = QVBoxLayout(self.shortcut_box)
        
        mod_layout = QHBoxLayout()
        self.chk_ctrl = QPushButton("CTRL")
        self.chk_shift = QPushButton("SHIFT")
        self.chk_alt = QPushButton("ALT")
        self.chk_win = QPushButton("WIN")
        for _mod_btn in (self.chk_ctrl, self.chk_shift, self.chk_alt, self.chk_win):
            _mod_btn.setCheckable(True)
            _mod_btn.setObjectName("modKeyToggle")
            mod_layout.addWidget(_mod_btn)
        mod_layout.addStretch()
        sc_layout.addLayout(mod_layout)

        key_layout = QHBoxLayout()
        self.lbl_target_key = QLabel("Hedef Tuş:")
        key_layout.addWidget(self.lbl_target_key)
        self.captured_key_code = 0
        self.txt_key = KeyCaptureLineEdit()
        self.txt_key.setObjectName("keyCapture")
        self.txt_key.setStyleSheet("font-family: 'Cascadia Mono', 'Consolas', 'Courier New', monospace; font-size: 13px;")
        self.txt_key.setPlaceholderText("Tıklayın, sonra tuşa basın (ok tuşları dahil)...")
        self.txt_key.keyCaptured.connect(self.on_key_captured)
        key_layout.addWidget(self.txt_key)
        sc_layout.addLayout(key_layout)
        self.layout.addWidget(self.shortcut_box)

        # 2. Text Macro Widget
        self.text_box = QGroupBox("Metin Makrosu")
        txt_layout = QVBoxLayout(self.text_box)
        self.txt_macro = QLineEdit()
        # Bounds how big a single SET_CONFIG line can get - see
        # mini_keyboard.ino's RX_BUFFER_SIZE comment for the SRAM budget
        # this is sized against (the device only stores the first 7 chars
        # in EEPROM regardless; this cap is about the serial message, not
        # macro usefulness).
        self.txt_macro.setMaxLength(2000)
        self.txt_macro.setPlaceholderText("Otomatik yazılacak metni girin...")
        txt_layout.addWidget(self.txt_macro)
        
        mode_radio_layout = QHBoxLayout()
        self.radio_type_keystroke = QRadioButton("⌨️ Karakter Karakter Yaz (Tuş Vuruşu)")
        self.radio_type_paste = QRadioButton("📋 Panodan Yapıştır (Ctrl+V)")
        self.radio_type_keystroke.setChecked(True)
        mode_radio_layout.addWidget(self.radio_type_keystroke)
        mode_radio_layout.addWidget(self.radio_type_paste)
        txt_layout.addLayout(mode_radio_layout)

        self.layout.addWidget(self.text_box)

        # 3. Media Key Widget
        self.media_box = QGroupBox("Medya Komutu")
        med_layout = QVBoxLayout(self.media_box)
        self.combo_media = QComboBox()
        # Oynatma komutlari icin hedef oynatici asagidan secilir (SMTC).
        for _label, _code in MEDIA_ITEMS_TR:
            self.combo_media.addItem(_label, _code)
        self.combo_media.currentIndexChanged.connect(self.update_player_visibility)
        med_layout.addWidget(self.combo_media)

        self.player_widget = QWidget()
        player_layout = QHBoxLayout(self.player_widget)
        player_layout.setContentsMargins(0, 6, 0, 0)
        self.lbl_player = QLabel("🎯 Hedef Oynatıcı:")
        player_layout.addWidget(self.lbl_player)
        self.combo_player = QComboBox()
        player_layout.addWidget(self.combo_player, stretch=1)
        self.btn_refresh_players = QPushButton("🔄")
        self.btn_refresh_players.setObjectName("smallIconBtn")
        self.btn_refresh_players.setToolTip("Açık oynatıcıları yenile")
        self.btn_refresh_players.setFixedSize(38, 32)
        self.btn_refresh_players.clicked.connect(lambda: self.refresh_players(force=True))
        player_layout.addWidget(self.btn_refresh_players)
        med_layout.addWidget(self.player_widget)
        # Liste arka planda yenilenince (bkz. _PlayerListCache) secimi koruyarak doldur.
        player_cache().updated.connect(self.refresh_players)
        self.refresh_players()
        self.update_player_visibility()
        self.layout.addWidget(self.media_box)

        # 4. Program & Game Launcher Widget
        self.app_box = QGroupBox("Program / Oyun Çalıştırıcı")
        app_layout = QVBoxLayout(self.app_box)

        search_bar_layout = QHBoxLayout()
        self.lbl_search_app = QLabel("🔍 Oyun / Uygulama Ara & Seç:")
        search_bar_layout.addWidget(self.lbl_search_app)
        
        self.txt_app_search = QLineEdit()
        self.txt_app_search.setPlaceholderText("🔍 Yazmaya başlayın (Örn: Chrome, Spotify, Steam)...")
        
        self.installed_apps = get_installed_windows_apps()
        app_names = [name for name, _ in self.installed_apps]
        
        self.completer = QCompleter(app_names, self)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.activated.connect(self.on_completer_app_selected)
        
        self.txt_app_search.setCompleter(self.completer)
        search_bar_layout.addWidget(self.txt_app_search, stretch=1)
        app_layout.addLayout(search_bar_layout)
        
        # Path Field & File Browser
        path_input_layout = QHBoxLayout()
        self.lbl_app_path = QLabel("Dosya Yolu:")
        path_input_layout.addWidget(self.lbl_app_path)
        self.txt_app_path = QLineEdit()
        # Windows MAX_PATH is 260; this stays under that while still giving
        # the same RX_BUFFER_SIZE headroom reasoning as txt_macro above.
        self.txt_app_path.setMaxLength(4096)
        self.txt_app_path.setPlaceholderText("Örn: C:\\Program Files\\Uygulama\\uygulama.exe")
        path_input_layout.addWidget(self.txt_app_path, stretch=1)

        self.btn_browse = QPushButton("📁 Gözat...")
        self.btn_browse.clicked.connect(self.browse_app_file)
        path_input_layout.addWidget(self.btn_browse)

        app_layout.addLayout(path_input_layout)

        self.chk_run_as_admin = ToggleSwitch("🛡️ Yönetici Olarak Çalıştır")
        self.chk_run_as_admin.setStyleSheet("color: #FFB454; font-weight: bold;")
        self.chk_run_as_admin.setToolTip(ADMIN_TIP_TR)
        app_layout.addWidget(self.chk_run_as_admin)

        # Extra Apps Container
        self.extra_apps_layout = QVBoxLayout()
        app_layout.addLayout(self.extra_apps_layout)
        self.extra_app_rows = []

        # Add Extra App Button
        self.btn_add_extra = QPushButton("➕ Ekstra Uygulama / Oyun Ekle")
        self.btn_add_extra.setStyleSheet("background-color: #17201A; border: 1px solid #24322A; color: #D98A54; font-weight: bold; padding: 6px 12px;")
        self.btn_add_extra.clicked.connect(lambda: self.add_extra_app_row())
        app_layout.addWidget(self.btn_add_extra)

        # Delay options container widget
        self.delay_widget = QWidget()
        delay_layout = QHBoxLayout(self.delay_widget)
        delay_layout.setContentsMargins(0, 4, 0, 0)
        self.chk_multi_delay = ToggleSwitch("⏱️ Sırayla Aç (Uygulamalar arasına gecikme koy)")
        self.chk_multi_delay.setStyleSheet("color: #D98A54; font-weight: bold;")
        delay_layout.addWidget(self.chk_multi_delay)

        self.lbl_delay = QLabel("Gecikme Süresi:")
        delay_layout.addWidget(self.lbl_delay)
        self.spin_multi_delay = QDoubleSpinBox()
        self.spin_multi_delay.setRange(0.1, 10.0)
        self.spin_multi_delay.setValue(1.0)
        self.spin_multi_delay.setSingleStep(0.5)
        self.spin_multi_delay.setSuffix(" saniye")
        self.spin_multi_delay.setMinimumWidth(100)
        delay_layout.addWidget(self.spin_multi_delay)
        delay_layout.addStretch()

        app_layout.addWidget(self.delay_widget)
        self.delay_widget.setVisible(False)
        self.layout.addWidget(self.app_box)

        self.layout.addStretch()
        self.on_type_changed(0)

    def on_key_captured(self, vk_code, display_name):
        self.captured_key_code = vk_code
        self.txt_key.setText(display_name)

    def on_completer_app_selected(self, selected_name):
        for name, path in self.installed_apps:
            if name == selected_name:
                self.txt_app_path.setText(os.path.normpath(path))
                break

    def update_player_visibility(self):
        """Hedef oynatici yalnizca Oynat/Sonraki/Onceki icin anlamli."""
        code = self.combo_media.currentData()
        visible = code in PLAYER_TARGETABLE_CODES
        self.player_widget.setVisible(visible)

    def refresh_players(self, keep="", force=False):
        """Hedef listesi: sistem geneli, muzik/video tarayicisi rolleri ve
        acik SMTC oynaticilari.

        Kayitli oynatici su an acik degilse listeye ayrica eklenir - aksi
        halde kullanici ayarlar sayfasini her actiginda secimi sessizce
        "Sistem geneli"ne donerdi. Kayitli kimlik PARCASI (ornegin "Chrome")
        acik bir oturumun tam kimligine uyuyorsa o secilir.
        """
        current = keep or (self.combo_player.currentData() if self.combo_player.count() else "")
        english = getattr(self, "language", "TR") == "EN"
        self.combo_player.blockSignals(True)
        self.combo_player.clear()
        self.combo_player.addItem(
            "System-wide (let Windows decide)" if english else "Sistem geneli (Windows karar versin)", "")
        unset = "not set" if english else "seçilmedi"
        for role, label in ((MUSIC_ROLE, "🎵 Music browser" if english else "🎵 Müzik tarayıcısı"),
                            (VIDEO_ROLE, "🎬 Video browser" if english else "🎬 Video tarayıcısı")):
            target = role_target(role)
            name = player_display_name(target) if target else unset
            self.combo_player.addItem(f"{label} ({name})", role)
        found = current in ("", MUSIC_ROLE, VIDEO_ROLE)
        players = list_players(force=force)
        if current and not found and all(key != current for key, _ in players):
            partial = [key for key, _ in players if current.lower() in key.lower()]
            if len(partial) == 1:
                current = partial[0]
        for key, display in players:
            self.combo_player.addItem(f"▶ {display}", key)
            if key == current:
                found = True
        if current and not found:
            closed = "(closed)" if english else "(şu an kapalı)"
            self.combo_player.addItem(f"⏸ {player_display_name(current)} {closed}", current)
        if current:
            idx = self.combo_player.findData(current)
            if idx >= 0:
                self.combo_player.setCurrentIndex(idx)
        self.combo_player.blockSignals(False)

    def browse_app_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            tr_en("Çalıştırılacak Oyun, Program veya Dosyayı Seç", "Choose a Game, Program or File to Launch"),
            "",
            tr_en("Çalıştırılabilir Dosyalar", "Executable Files") + " (*.exe *.bat *.cmd *.lnk *.url *.py);;"
            + tr_en("Tüm Dosyalar", "All Files") + " (*.*)"
        )
        if file_path:
            self.txt_app_path.setText(os.path.normpath(file_path))
            self.txt_app_search.setText(os.path.basename(file_path))

    def add_extra_app_row(self, initial_path=""):
        idx = len(self.extra_app_rows) + 2
        row_frame = QFrame()
        row_frame.setObjectName("extraAppRow")
        # Yalnizca cercevenin kendisi: "QFrame { }" secicisi QLabel'lara da
        # (QLabel bir QFrame) kenarlik ve dolgu veriyordu.
        row_frame.setStyleSheet("QFrame#extraAppRow { background-color: #131A16; border: 1px solid #24322A; border-radius: 6px; }")
        row_layout = QVBoxLayout(row_frame)
        row_layout.setContentsMargins(8, 8, 8, 8)

        header_layout = QHBoxLayout()
        lbl_header = QLabel()
        header_layout.addWidget(lbl_header)
        header_layout.addStretch()
        
        btn_remove = QPushButton()
        btn_remove.setStyleSheet("background-color: #E0524A; color: #EDEAE0; font-weight: bold; padding: 2px 10px;")
        header_layout.addWidget(btn_remove)
        row_layout.addLayout(header_layout)

        search_layout = QHBoxLayout()
        lbl_search = QLabel()
        search_layout.addWidget(lbl_search)
        txt_search = QLineEdit()
        app_names = [name for name, _ in self.installed_apps]
        completer_extra = QCompleter(app_names, self)
        completer_extra.setFilterMode(Qt.MatchFlag.MatchContains)
        completer_extra.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer_extra.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        txt_search.setCompleter(completer_extra)
        search_layout.addWidget(txt_search, stretch=1)
        row_layout.addLayout(search_layout)

        path_layout = QHBoxLayout()
        lbl_path = QLabel()
        path_layout.addWidget(lbl_path)
        txt_path = QLineEdit()
        if initial_path:
            txt_path.setText(initial_path)
            matched_name = get_app_name_for_path(initial_path)
            if matched_name:
                txt_search.setText(matched_name)

        path_layout.addWidget(txt_path, stretch=1)

        btn_browse = QPushButton()
        def browse_extra():
            f_path, _ = QFileDialog.getOpenFileName(
                self, tr_en("Ekstra Uygulama Seç", "Choose Extra App"), "",
                tr_en("Çalıştırılabilir Dosyalar", "Executable Files") + " (*.exe *.bat *.cmd *.lnk *.url *.py);;"
                + tr_en("Tüm Dosyalar", "All Files") + " (*.*)"
            )
            if f_path:
                txt_path.setText(os.path.normpath(f_path))
                txt_search.setText(os.path.basename(f_path))
        btn_browse.clicked.connect(browse_extra)
        path_layout.addWidget(btn_browse)

        row_layout.addLayout(path_layout)

        def on_extra_selected(selected_name):
            for name, path in self.installed_apps:
                if name == selected_name:
                    txt_path.setText(os.path.normpath(path))
                    break
        completer_extra.activated.connect(on_extra_selected)

        row_data = {
            "frame": row_frame,
            "txt_search": txt_search,
            "txt_path": txt_path,
            "idx": idx,
            "lbl_header": lbl_header,
            "btn_remove": btn_remove,
            "lbl_search": lbl_search,
            "lbl_path": lbl_path,
            "btn_browse": btn_browse,
        }
        self._translate_extra_row(row_data)
        
        def remove_this_row():
            self.extra_apps_layout.removeWidget(row_frame)
            row_frame.hide()
            row_frame.deleteLater()
            if row_data in self.extra_app_rows:
                self.extra_app_rows.remove(row_data)
            self.update_delay_visibility()

        btn_remove.clicked.connect(remove_this_row)

        self.extra_apps_layout.addWidget(row_frame)
        self.extra_app_rows.append(row_data)
        self.update_delay_visibility()

    def clear_extra_app_rows(self):
        for row in list(self.extra_app_rows):
            self.extra_apps_layout.removeWidget(row["frame"])
            # deleteLater olay dongusunu bekler; o ana kadar eski satir yeni
            # satirlarin ustunde gorunmesin.
            row["frame"].hide()
            row["frame"].deleteLater()
        self.extra_app_rows.clear()
        self.update_delay_visibility()

    def update_delay_visibility(self):
        self.delay_widget.setVisible(len(self.extra_app_rows) > 0)

    def on_type_changed(self, index):
        self.shortcut_box.setVisible(index == 1)
        self.text_box.setVisible(index == 2)
        self.media_box.setVisible(index == 3)
        self.app_box.setVisible(index == 5)

    def retranslate_ui(self, lang_code="TR"):
        self.language = lang_code
        if lang_code == "EN":
            self.lbl_action_type.setText("ACTION TYPE")
            self.combo_type.setItemText(0, "Disabled")
            self.combo_type.setItemText(1, "Keyboard Shortcut")
            self.combo_type.setItemText(2, "Text Macro")
            self.combo_type.setItemText(3, "Media Control")
            self.combo_type.setItemText(4, "Mode Switcher (Next Mode)")
            self.combo_type.setItemText(5, "🚀 Launch Program / Game")
            
            self.shortcut_box.setTitle("Shortcut Settings")
            self.lbl_target_key.setText("Target Key:")
            self.txt_key.setPlaceholderText("Click, then press a key (arrows included)...")
            
            self.text_box.setTitle("Text Macro")
            self.txt_macro.setPlaceholderText("Enter text to type automatically...")
            self.radio_type_keystroke.setText("⌨️ Type Character by Character")
            self.radio_type_paste.setText("📋 Paste from Clipboard (Ctrl+V)")
            
            self.media_box.setTitle("Media Command")
            for _i, (_label, _code) in enumerate(MEDIA_ITEMS_EN):
                self.combo_media.setItemText(_i, _label)
            self.lbl_player.setText("🎯 Target Player:")
            self.refresh_players()
            
            self.app_box.setTitle("Program / Game Launcher")
            self.lbl_search_app.setText("🔍 Search Game / Program:")
            self.txt_app_search.setPlaceholderText("🔍 Start typing (Ex: Chrome, Spotify, Steam)...")
            self.lbl_app_path.setText("File Path:")
            self.txt_app_path.setPlaceholderText("Ex: C:\\Program Files\\App\\app.exe")
            self.btn_browse.setText("📁 Browse...")
            self.btn_add_extra.setText("➕ Add Extra App / Game")
            self.chk_run_as_admin.setText("🛡️ Run as Administrator")
            self.chk_run_as_admin.setToolTip(ADMIN_TIP_EN)
            self.chk_multi_delay.setText("⏱️ Launch Sequentially (Delay between apps)")
            self.lbl_delay.setText("Delay:")
            self.spin_multi_delay.setSuffix(" s")
            self.btn_refresh_players.setToolTip("Refresh open players")
        else:
            self.lbl_action_type.setText("EYLEM TÜRÜ")
            self.combo_type.setItemText(0, "Devre Dışı")
            self.combo_type.setItemText(1, "Kısayol Tuşu")
            self.combo_type.setItemText(2, "Metin Makrosu")
            self.combo_type.setItemText(3, "Medya Kontrolü")
            self.combo_type.setItemText(4, "Mod Değiştirici (Sonraki Mod)")
            self.combo_type.setItemText(5, "🚀 Program / Oyun Çalıştır")
            
            self.shortcut_box.setTitle("Kısayol Ayarları")
            self.lbl_target_key.setText("Hedef Tuş:")
            self.txt_key.setPlaceholderText("Tıklayın, sonra tuşa basın (ok tuşları dahil)...")
            
            self.text_box.setTitle("Metin Makrosu")
            self.txt_macro.setPlaceholderText("Otomatik yazılacak metni girin...")
            self.radio_type_keystroke.setText("⌨️ Karakter Karakter Yaz (Tuş Vuruşu)")
            self.radio_type_paste.setText("📋 Panodan Yapıştır (Ctrl+V)")
            
            self.media_box.setTitle("Medya Komutu")
            for _i, (_label, _code) in enumerate(MEDIA_ITEMS_TR):
                self.combo_media.setItemText(_i, _label)
            self.lbl_player.setText("🎯 Hedef Oynatıcı:")
            self.refresh_players()
            
            self.app_box.setTitle("Program / Oyun Çalıştırıcı")
            self.lbl_search_app.setText("🔍 Oyun / Uygulama Ara & Seç:")
            self.txt_app_search.setPlaceholderText("🔍 Yazmaya başlayın (Örn: Chrome, Spotify, Steam)...")
            self.lbl_app_path.setText("Dosya Yolu:")
            self.txt_app_path.setPlaceholderText("Örn: C:\\Program Files\\Uygulama\\uygulama.exe")
            self.btn_browse.setText("📁 Gözat...")
            self.btn_add_extra.setText("➕ Ekstra Uygulama / Oyun Ekle")
            self.chk_run_as_admin.setText("🛡️ Yönetici Olarak Çalıştır")
            self.chk_run_as_admin.setToolTip(ADMIN_TIP_TR)
            self.chk_multi_delay.setText("⏱️ Sırayla Aç (Uygulamalar arasına gecikme koy)")
            self.lbl_delay.setText("Gecikme Süresi:")
            self.spin_multi_delay.setSuffix(" saniye")
            self.btn_refresh_players.setToolTip("Açık oynatıcıları yenile")
        for row in self.extra_app_rows:
            self._translate_extra_row(row)

    def _translate_extra_row(self, row):
        row["lbl_header"].setText(tr_en(f"➕ {row['idx']}. Ekstra Uygulama:", f"➕ Extra App {row['idx']}:"))
        row["btn_remove"].setText(tr_en("🗑️ Sil", "🗑️ Remove"))
        row["lbl_search"].setText(tr_en("🔍 Ara:", "🔍 Search:"))
        row["txt_search"].setPlaceholderText(tr_en("Yazmaya başlayın (Örn: Discord, Spotify, Steam)...",
                                                   "Start typing (Ex: Discord, Spotify, Steam)..."))
        row["lbl_path"].setText(tr_en("Dosya Yolu:", "File Path:"))
        row["txt_path"].setPlaceholderText(tr_en("Örn: C:\\Program Files\\Discord\\Update.exe",
                                                 "Ex: C:\\Program Files\\Discord\\Update.exe"))
        row["btn_browse"].setText(tr_en("📁 Gözat...", "📁 Browse..."))

    def reset_fields(self):
        """Tum alanlari bosaltir.

        set_data yalnizca kayitli turun alanlarini doldurur; diger alanlar
        onceki secili tustan kaliyordu. Ornegin kisayolu olan bir tustan
        bos bir tusa gecip turu "Kisayol" yapinca eski kisayol, "Program"
        yapinca eski tusun ekstra uygulama satirlari hazir geliyor ve fark
        edilmeden bu tusa da kaydediliyordu.
        """
        for btn in (self.chk_ctrl, self.chk_shift, self.chk_alt, self.chk_win):
            btn.setChecked(False)
        self.captured_key_code = 0
        self.txt_key.clear()
        self.txt_macro.clear()
        self.radio_type_keystroke.setChecked(True)
        self.combo_media.setCurrentIndex(0)
        if self.combo_player.count():
            self.combo_player.setCurrentIndex(0)
        self.txt_app_search.clear()
        self.txt_app_path.clear()
        self.clear_extra_app_rows()
        self.chk_multi_delay.setChecked(False)
        self.spin_multi_delay.setValue(1.0)
        self.chk_run_as_admin.setChecked(False)

    def set_data(self, data):
        self.reset_fields()
        action_type = data.get("type", "none")
        if action_type == "shortcut":
            self.combo_type.setCurrentIndex(1)
            mods = data.get("mods", 0)
            self.chk_ctrl.setChecked(bool(mods & 1))
            self.chk_shift.setChecked(bool(mods & 2))
            self.chk_alt.setChecked(bool(mods & 4))
            self.chk_win.setChecked(bool(mods & 8))
            key_code = data.get("key", 0)
            self.captured_key_code = key_code
            self.txt_key.setText(vk_to_display_name(key_code))
        elif action_type == "text":
            self.combo_type.setCurrentIndex(2)
            self.txt_macro.setText(data.get("text", ""))
            if data.get("mode") == "paste":
                self.radio_type_paste.setChecked(True)
            else:
                self.radio_type_keystroke.setChecked(True)
        elif action_type == "media":
            self.combo_type.setCurrentIndex(3)
            # Eski profil surumlerinin 7/8/9 kodlari 4/5/6'ya cevrilir.
            code, player = media_action_target(data)
            idx = self.combo_media.findData(code)
            if idx >= 0:
                self.combo_media.setCurrentIndex(idx)
            self.refresh_players(keep=player)
            self.update_player_visibility()
        elif action_type == "mode_switch":
            self.combo_type.setCurrentIndex(4)
        elif action_type == "app":
            self.combo_type.setCurrentIndex(5)
            paths = data.get("paths", [])
            if not paths and data.get("path"):
                paths = [data.get("path")]

            primary_p = paths[0] if paths else ""
            self.txt_app_path.setText(primary_p)
            matched_name = get_app_name_for_path(primary_p)
            if matched_name:
                self.txt_app_search.setText(matched_name)

            self.clear_extra_app_rows()
            for p in paths[1:]:
                self.add_extra_app_row(p)

            self.update_delay_visibility()
            self.chk_multi_delay.setChecked(data.get("use_delay", False))
            self.spin_multi_delay.setValue(data.get("delay_sec", 1.0))
            self.chk_run_as_admin.setChecked(data.get("run_as_admin", False))
        else:
            self.combo_type.setCurrentIndex(0)

    def get_data(self):
        idx = self.combo_type.currentIndex()
        if idx == 1:
            mods = 0
            if self.chk_ctrl.isChecked(): mods |= 1
            if self.chk_shift.isChecked(): mods |= 2
            if self.chk_alt.isChecked(): mods |= 4
            if self.chk_win.isChecked(): mods |= 8
            return {"type": "shortcut", "mods": mods, "key": self.captured_key_code}
        elif idx == 2:
            return {
                "type": "text",
                "text": self.txt_macro.text(),
                "mode": "paste" if self.radio_type_paste.isChecked() else "type"
            }
        elif idx == 3:
            code = self.combo_media.currentData()
            player = self.combo_player.currentData() or ""
            if code not in PLAYER_TARGETABLE_CODES:
                player = ""
            return {"type": "media", "code": code, "player": player}
        elif idx == 4:
            return {"type": "mode_switch"}
        elif idx == 5:
            paths = []
            primary_p = self.txt_app_path.text().strip()
            if primary_p:
                paths.append(primary_p)
            for row in self.extra_app_rows:
                p = row["txt_path"].text().strip()
                if p:
                    paths.append(p)
            return {
                "type": "app",
                "path": primary_p,
                "paths": paths,
                "use_delay": self.chk_multi_delay.isChecked(),
                "delay_sec": self.spin_multi_delay.value(),
                "run_as_admin": self.chk_run_as_admin.isChecked()
            }
        else:
            return {"type": "none"}


class KeycapButton(QPushButton):
    """9-key grid button. Content (number / glyph / action-type tag) is laid
    out with child QLabels instead of setText(), so each zone can be styled
    and recolored independently - matching the approved keycap mockup."""

    def __init__(self, key_number, parent=None):
        super().__init__(parent)
        self._release_timer = None
        self._press_anim = None
        self._is_home = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 8)
        layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        self.num_label = QLabel(str(key_number))
        self.num_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        top_row.addWidget(self.num_label)
        top_row.addStretch()
        layout.addLayout(top_row)

        self.glyph_label = QLabel("—")
        self.glyph_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.glyph_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.glyph_label, stretch=1)

        self.tag_label = QLabel(tr_en("BOŞ", "EMPTY"))
        self.tag_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tag_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.tag_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._apply_tag_style(None)
        self.set_selected(False)

    def set_content(self, glyph, tag_text, tag_color_hex, icon_pixmap=None):
        if icon_pixmap is not None:
            self.glyph_label.setPixmap(icon_pixmap)
        else:
            self.glyph_label.setText(glyph)
        self.tag_label.setText(tag_text)
        self._apply_tag_style(tag_color_hex)

    def _apply_tag_style(self, color_hex):
        if color_hex:
            c = QColor(color_hex)
            bg = f"rgba({c.red()}, {c.green()}, {c.blue()}, 35)"
            self.tag_label.setStyleSheet(
                f"color: {color_hex}; background-color: {bg}; border-radius: 6px; "
                f"padding: 1px 5px; font-family: 'Cascadia Mono', 'Consolas', monospace; "
                f"font-size: 8px; font-weight: 700;"
            )
        else:
            self.tag_label.setStyleSheet(
                "color: #3A4C40; background-color: transparent; padding: 1px 5px; "
                "font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 8px; font-weight: 700;"
            )

    def set_home(self, is_home):
        """Marks the center key with a small tactile nub, like the home-row
        bump on a real keyboard - it's the most-used key on this pad."""
        self._is_home = is_home
        self.update()

    def set_selected(self, selected):
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        accent = "#F2A96C" if selected else "#525A50"
        self.num_label.setStyleSheet(
            f"color: {accent}; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px;"
        )
        glyph_color = "#F2A96C" if selected else "#DCE0DA"
        self.glyph_label.setStyleSheet(f"font-size: 19px; color: {glyph_color};")

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._is_home:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#525A50"))
            bar_w, bar_h = 14, 2
            painter.drawRoundedRect(
                QRectF(self.width() / 2 - bar_w / 2, self.height() - 12, bar_w, bar_h), 1, 1
            )
            painter.end()

    def mousePressEvent(self, event):
        self.animate_press()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.animate_release()
        super().mouseReleaseEvent(event)

    def animate_press(self):
        self._run_press_anim(pressed=True)

    def animate_release(self):
        self._run_press_anim(pressed=False)

    def _run_press_anim(self, pressed):
        """Darkens the keycap instantly (a reliable QSS property toggle) and
        smoothly flares its glow outward while held, reverting the instant
        it's released. Deliberately avoids animating geometry/size: this
        widget has a fixed size (setFixedSize) inside a QGridLayout, so a
        geometry animation gets fought and snapped back by the layout engine
        while still held. Animating the QGraphicsDropShadowEffect's blur
        radius is pure compositing - the layout never touches it, so the
        pressed look reliably persists for the whole hold."""
        self.setProperty("pressedAnim", pressed)
        self.style().unpolish(self)
        self.style().polish(self)

        effect = self.graphicsEffect()
        if not isinstance(effect, QGraphicsDropShadowEffect):
            effect = QGraphicsDropShadowEffect(self)
            effect.setOffset(0, 0)
            self.setGraphicsEffect(effect)

        # Tek animasyon nesnesi yeniden kullaniliyor. Eskiden her basista
        # (bas + birak) iki yeni QPropertyAnimation bu dugmeye cocuk olarak
        # ekleniyor ve hic silinmiyordu: 500 basista dugmede 1000 nesne
        # birikiyordu (surekli acik kalan bir programda sinirsiz buyume).
        anim = self._press_anim
        if anim is None or anim.targetObject() is not effect:
            anim = QPropertyAnimation(effect, b"blurRadius", self)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._press_anim = anim
        anim.stop()

        idle_color_hex = getattr(self, "_glow_color_hex", None)
        idle_blur = 24 if idle_color_hex else 0

        if pressed:
            press_color = QColor(idle_color_hex or "#D98A54")
            press_color.setAlpha(220)
            effect.setColor(press_color)
            end_blur, duration = 40, 80
        else:
            restore_color = QColor(idle_color_hex) if idle_color_hex else QColor(0, 0, 0, 0)
            if idle_color_hex:
                restore_color.setAlpha(200)
            effect.setColor(restore_color)
            end_blur, duration = idle_blur, 150

        anim.setStartValue(effect.blurRadius())
        anim.setEndValue(end_blur)
        anim.setDuration(duration)
        anim.start()


class PanelFrame(QFrame):
    """Bordered panel with an uppercase header row (title + optional hint)
    and four small corner 'mounting holes', standing in for QGroupBox where
    the PCB-panel look from the approved mockup is wanted. Exposes
    setTitle()/title() so existing QGroupBox call sites keep working
    unchanged, and a body_layout that callers add their real content into."""

    def __init__(self, title="", hint="", parent=None):
        super().__init__(parent)
        self.setObjectName("panelFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._title = title

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(2, 2, 2, 0)
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("panelTitle")
        head.addWidget(self.title_label)
        head.addStretch()
        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("panelHint")
        head.addWidget(self.hint_label)
        outer.addLayout(head)

        self.body_layout = QVBoxLayout()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.body_layout)

    def setTitle(self, text):
        self._title = text
        self.title_label.setText(text.upper())

    def title(self):
        return self._title

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#3A4C40"), 1))
        painter.setBrush(QColor("#0A0F0C"))
        r, m = 3.0, 9.0
        w, h = self.width(), self.height()
        for cx, cy in ((m, m), (w - m, m), (m, h - m), (w - m, h - m)):
            painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        painter.end()


class SpecCard(QFrame):
    """Flat settings card with a small uppercase eyebrow title, matching
    the approved mockup's ".spec-card" - like PanelFrame but without the
    PCB mounting holes, which only belong on the physical-keypad panels."""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setObjectName("specCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 18)
        outer.setSpacing(12)

        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("panelTitle")
        outer.addWidget(self.title_label)

        self.body_layout = QVBoxLayout()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(0)
        outer.addLayout(self.body_layout)
        # Ayni satirdaki daha uzun kartla esitlenen kisa kartin icerigi
        # ustte kalsin (yoksa baslik ve icerik kartin ortasina dagiliyordu).
        outer.addStretch()

    def setTitle(self, text):
        self.title_label.setText(text.upper())


class SegmentedControl(QWidget):
    """Exclusive row of flat buttons sharing one pill border, replacing a
    QRadioButton/QComboBox pair for small fixed option sets (language,
    default COM port) - matches the mockup's ".segmented" control."""

    currentChanged = pyqtSignal(str)

    def __init__(self, options, parent=None):
        super().__init__(parent)  # options: list of (value, label)
        self._buttons = {}
        self._value = options[0][0] if options else None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        frame = QFrame()
        frame.setObjectName("segmented")
        frame_layout = QHBoxLayout(frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        for value, label in options:
            btn = QPushButton(label)
            btn.setObjectName("segBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, v=value: self.set_value(v, emit=True))
            frame_layout.addWidget(btn)
            self._buttons[value] = btn

        layout.addWidget(frame)
        layout.addStretch()
        self.set_value(self._value, emit=False)

    def set_value(self, value, emit=True):
        if value not in self._buttons:
            return
        self._value = value
        for v, btn in self._buttons.items():
            btn.setChecked(v == value)
        if emit:
            self.currentChanged.emit(value)

    def value(self):
        return self._value


class Stepper(QWidget):
    """Bordered "- value +" control replacing a QComboBox for a small
    bounded integer (mode count), matching the mockup's ".stepper"."""

    valueChanged = pyqtSignal(int)

    def __init__(self, minimum=1, maximum=5, value=3, parent=None):
        super().__init__(parent)
        self._min, self._max = minimum, maximum
        self._value = value

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        frame = QFrame()
        frame.setObjectName("stepperFrame")
        frame_layout = QHBoxLayout(frame)
        frame_layout.setContentsMargins(14, 6, 14, 6)
        frame_layout.setSpacing(14)

        self.btn_minus = QPushButton("−")
        self.btn_minus.setObjectName("stepperBtn")
        self.btn_minus.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_minus.clicked.connect(lambda: self.set_value(self._value - 1))
        frame_layout.addWidget(self.btn_minus)

        self.lbl_value = QLabel(str(value))
        self.lbl_value.setObjectName("stepperVal")
        self.lbl_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frame_layout.addWidget(self.lbl_value)

        self.btn_plus = QPushButton("+")
        self.btn_plus.setObjectName("stepperBtn")
        self.btn_plus.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_plus.clicked.connect(lambda: self.set_value(self._value + 1))
        frame_layout.addWidget(self.btn_plus)

        layout.addWidget(frame)
        layout.addStretch()

    def set_value(self, value, emit=True):
        value = max(self._min, min(self._max, value))
        if value == self._value and self.lbl_value.text() == str(value):
            return
        self._value = value
        self.lbl_value.setText(str(value))
        if emit:
            self.valueChanged.emit(value)

    def value(self):
        return self._value


def row_divider():
    """A 1px line matching the mockup's border-top row divider
    (".mode-row"/".check-row"), inserted between rows in a settings card."""
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background-color: #24322A; border: none;")
    return line


class ModePill(QPushButton):
    """Mode-select pill: a colored LED dot plus label, matching the
    approved instrument-panel mockup's mode toggles."""

    def __init__(self, label, color_hex="#D98A54", parent=None):
        super().__init__(parent)
        self.setObjectName("modePill")
        self._color = color_hex
        self._on = False
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 14, 0)
        layout.setSpacing(8)

        self.led = QLabel()
        self.led.setFixedSize(9, 9)
        self.led.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.led)

        self.text_label = QLabel(label)
        self.text_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.text_label)

        self._refresh()

    def sizeHint(self):
        # QPushButton.sizeHint() computes from its own text/icon, ignoring
        # any child layout - since this button's real content lives in the
        # led+text_label layout instead of setText(), sizeHint() has to be
        # forwarded explicitly or the Fixed size policy below shrinks the
        # button down to whatever tiny size the (empty-text) default gives.
        return self.layout().sizeHint()

    def minimumSizeHint(self):
        return self.layout().sizeHint()

    def set_color(self, color_hex):
        self._color = color_hex
        self._refresh()

    def set_on(self, is_on):
        self._on = is_on
        self._refresh()

    def _refresh(self):
        if self._on:
            tinted_bg = blend_hex("#131A16", self._color, 0.18)
            self.led.setStyleSheet(f"border-radius: 4px; background-color: {self._color};")
            glow = QGraphicsDropShadowEffect(self.led)
            glow.setColor(QColor(self._color))
            glow.setBlurRadius(14)
            glow.setOffset(0, 0)
            self.led.setGraphicsEffect(glow)
            self.text_label.setStyleSheet("color: #EDEAE0; font-weight: 600; font-size: 12.5px;")
            self.setStyleSheet(
                f"QPushButton#modePill {{ border: 1px solid {self._color}; background-color: {tinted_bg}; }}"
            )
        else:
            self.led.setStyleSheet("border-radius: 4px; background-color: #525A50;")
            self.led.setGraphicsEffect(None)
            self.text_label.setStyleSheet("color: #8B9289; font-weight: 600; font-size: 12.5px;")
            self.setStyleSheet(
                "QPushButton#modePill { border: 1px solid #24322A; background-color: #131A16; }"
            )


class TraceConnector(QWidget):
    """Decorative copper trace linking the keypad panel to the editor
    panel, echoing the approved PCB-style mockup - purely visual, colored
    to match whichever mode is currently active."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(46)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._color = "#D98A54"

    def set_color(self, color_hex):
        self._color = color_hex
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._color)
        y = self.height() // 2

        painter.setPen(QPen(color, 2))
        painter.drawLine(6, y, self.width() - 6, y)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        for x in (6, self.width() - 6):
            painter.drawEllipse(QRectF(x - 3, y - 3, 6, 6))
        painter.end()


class BrandMark(QLabel):
    """Small 3x3 dot glyph echoing the physical keypad, used as the app's
    wordmark icon in the top bar."""

    def __init__(self, size=30, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#3A4C40"), 1))
        painter.setBrush(QColor("#0E1411"))
        painter.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), 7, 7)
        dot, gap, start = 4, 3, 6
        painter.setPen(Qt.PenStyle.NoPen)
        for row in range(3):
            for col in range(3):
                x = start + col * (dot + gap)
                y = start + row * (dot + gap)
                is_center = row == 1 and col == 1
                painter.setBrush(QColor("#D98A54") if is_center else QColor("#525A50"))
                painter.drawRoundedRect(QRectF(x, y, dot, dot), 1, 1)
        painter.end()
        self.setPixmap(pixmap)


class MainWindow(QMainWindow):
    # _start_show_ui_listener'daki dinleyici thread'i tarafindan tetiklenir
    # (bkz. dosyanin ustu) - baska bir thread'den emit edilse de Qt bunu
    # otomatik olarak ana thread'e kuyruklar, bu yuzden guvenlidir.
    show_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.show_requested.connect(self.show_window_from_tray)
        self.setWindowTitle(f"Mini Keyboard Configurator {APP_VERSION}")
        self.resize(900, 740)
        self.setStyleSheet(DARK_STYLE)

        self.popups = []

        self.active_mode = 0
        self.selected_key_id = -1
        # Kartin bu baglantida hangi modda hangi tuslari aldigi
        # (bkz. upload_config_to_device).
        self._device_state = {}
        self.load_local_user_config()

        self.worker = SerialWorker()
        self.worker.connected_signal.connect(self.on_connected_signal)
        self.worker.key_pressed_signal.connect(self.on_physical_key_press)
        self.worker.mode_changed_signal.connect(self.on_hardware_mode_changed)
        self.worker.command_response_signal.connect(self.on_command_response)
        self.worker.status_signal.connect(self.on_worker_status)
        self.worker.start()

        self.is_connected = False
        self.flasher_worker = None
        self.progress_dialog = None

        self.init_ui()
        app_icon = create_app_icon()
        self.setWindowIcon(app_icon)
        self.init_tray_icon()
        self.refresh_ports()
        
        QTimer.singleShot(2500, save_and_update_desktop_shortcut)
        
        QTimer.singleShot(300, self.auto_connect_default_port)
        self.apply_language()
        self.switch_mode(self.active_mode, sync_device=False, show_popup=False, save_current=False)

    @property
    def config_data(self):
        return self.modes[self.active_mode]

    def init_tray_icon(self):
        self._force_closing = False
        self.tray_icon = QSystemTrayIcon(self)
        app_icon = create_app_icon()
        self.tray_icon.setIcon(app_icon)
        self.tray_icon.setToolTip(f"Mini Keyboard Configurator {APP_VERSION}")

        tray_menu = QMenu()
        show_action = self.tray_show_action = QAction(tr_en("📌 Göster / Yapılandır", "📌 Show / Configure"), self)
        show_action.triggered.connect(self.show_window_from_tray)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        quit_action = self.tray_quit_action = QAction(tr_en("❌ Tamamen Çıkış Yap", "❌ Quit"), self)
        quit_action.triggered.connect(self.force_quit_application)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.show()

    def show_window_from_tray(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger or reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window_from_tray()

    def closeEvent(self, event):
        if getattr(self, '_force_closing', False):
            event.accept()
        elif not getattr(self, "tray_close", True):
            event.ignore()
            self.force_quit_application()
        else:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "Mini Keyboard",
                tr_en("Program arka planda çalışmaya devam ediyor. Simgeye çift tıklayarak açabilirsiniz.",
                      "Still running in the background. Double-click the tray icon to open it."),
                QSystemTrayIcon.MessageIcon.Information,
                2500
            )

    def force_quit_application(self):
        self._force_closing = True
        if self.worker:
            try:
                self.worker.stop_worker()
            except Exception:
                pass
        shutdown_actions()
        _smtc_sunucu.kapat()
        self.tray_icon.hide()
        QApplication.quit()

    def get_mode_display_name(self, mode_idx):
        """Custom name set in Settings if any, else the default 'MOD n'."""
        names = getattr(self, "mode_names", [])
        if 0 <= mode_idx < len(names) and names[mode_idx].strip():
            return names[mode_idx].strip()
        t = TRANSLATIONS.get(getattr(self, "language", "TR"), TRANSLATIONS["TR"])
        prefix = t.get("mod_prefix", "MOD")
        return f"{prefix} {mode_idx + 1}"

    def show_multi_monitor_popups(self, mode_idx):
        if not getattr(self, "popup_notify", True):
            return
        screens = QGuiApplication.screens()
        while len(self.popups) < len(screens):
            self.popups.append(ModeNotificationPopup())

        color = self.mode_colors[mode_idx] if mode_idx < len(self.mode_colors) else "#F2A96C"
        name = self.get_mode_display_name(mode_idx)

        for idx, screen in enumerate(screens):
            self.popups[idx].show_mode(mode_idx, mode_color=color, mode_name=name, target_screen=screen)

    @staticmethod
    def _normalized_modes(raw_modes, fallback=None):
        """Her zaman 5 mod x 9 tus dondurur.

        Eksik modlu bir profil (ornegin 3 modla disa aktarilmis) dogrudan
        self.modes'a konuyordu; sonra mod sayisi artirilip 4. moda
        gecilince IndexError ile arayuz cokuyordu.
        """
        empty = lambda: [{"type": "none"} for _ in range(9)]
        out = [list(m) for m in (fallback or [])][:5]
        while len(out) < 5:
            out.append(empty())
        if isinstance(raw_modes, list):
            for idx, m in enumerate(raw_modes[:5]):
                if isinstance(m, list) and len(m) == 9 and all(isinstance(k, dict) for k in m):
                    out[idx] = m
        return out

    def load_local_user_config(self):
        self.default_com_port = ""
        self.language = "TR"
        self.mode_count = 3
        self.mode_colors = ["#D98A54", "#FF6B6B", "#8CFFC7", "#4FB8E8", "#B57BF2"]
        self.mode_names = ["", "", "", "", ""]
        self.modes = [[{"type": "none"} for _ in range(9)] for _ in range(5)]
        self.tray_close = True
        self.popup_notify = True
        self.music_player = ""
        self.video_player = ""
        self.board = "promicro"

        config_path = USER_CONFIG_FILE if os.path.exists(USER_CONFIG_FILE) else EXAMPLE_CONFIG_FILE
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        # Always start on MOD 1 - don't restore whatever mode
                        # was active when the program last closed.
                        self.active_mode = 0
                        self.mode_count = max(1, min(5, data.get("mode_count", 3)))
                        self.default_com_port = data.get("default_com_port", "")
                        self.language = data.get("language", "TR")
                        self.tray_close = bool(data.get("tray_close", True))
                        self.popup_notify = bool(data.get("popup_notify", True))
                        self.music_player = str(data.get("music_player", "") or "")
                        self.video_player = str(data.get("video_player", "") or "")
                        self.board = "nano" if data.get("board") == "nano" else "promicro"
                        saved_colors = data.get("mode_colors", [])
                        if isinstance(saved_colors, list) and len(saved_colors) > 0:
                            for idx, c in enumerate(saved_colors):
                                if idx < 5:
                                    self.mode_colors[idx] = c
                        saved_names = data.get("mode_names", [])
                        if isinstance(saved_names, list) and len(saved_names) > 0:
                            for idx, n in enumerate(saved_names):
                                if idx < 5 and isinstance(n, str):
                                    self.mode_names[idx] = n
                        self.modes = self._normalized_modes(data.get("modes", []), self.modes)
                    elif isinstance(data, list) and len(data) == 9:
                        self.modes[0] = data
            except Exception:
                pass
        set_role_targets(self.music_player, self.video_player)
        set_language(self.language)
        SerialWorker.BOARD = self.board

    def save_local_user_config(self):
        try:
            # Once gecici dosyaya yazip sonra os.replace ile atomik olarak
            # asil dosyanin yerine koyuyoruz. Dogrudan USER_CONFIG_FILE'a
            # yazmak, yazma sirasinda uygulama cokerse veya elektrik kesilirse
            # dosyayi yarim/bozuk birakabiliyordu - bir sonraki acilista
            # load_local_user_config() json.load() hatasini genis bir
            # except Exception: pass ile yutup TUM kullanici profilini
            # (renkler, isimler, tus atamalari) sessizce varsayilana
            # dondururdu. os.replace ayni diskte atomik oldugu icin (Windows
            # NTFS dahil) ya eski dosya tam kalir ya da yeni dosya tam yazilir,
            # yarim durum olmaz.
            payload = json.dumps({
                "mode_count": getattr(self, "mode_count", 3),
                "default_com_port": getattr(self, "default_com_port", ""),
                "language": getattr(self, "language", "TR"),
                "mode_colors": getattr(self, "mode_colors", ["#D98A54", "#FF6B6B", "#8CFFC7", "#4FB8E8", "#B57BF2"]),
                "mode_names": getattr(self, "mode_names", ["", "", "", "", ""]),
                "tray_close": getattr(self, "tray_close", True),
                "popup_notify": getattr(self, "popup_notify", True),
                "music_player": getattr(self, "music_player", ""),
                "video_player": getattr(self, "video_player", ""),
                "board": getattr(self, "board", "promicro"),
                "modes": self.modes
            }, indent=2, ensure_ascii=False)
            # Bu fonksiyon her tus seciminde cagriliyor; icerik ayniysa
            # diske fsync'li yazma atlanir.
            if payload == getattr(self, "_last_saved_payload", None):
                return
            tmp_path = USER_CONFIG_FILE + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, USER_CONFIG_FILE)
            self._last_saved_payload = payload
        except Exception:
            _logger.exception("Ayar dosyasi kaydedilemedi.")

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # 1. Top Navigation & Connection Bar (Compact)
        top_bar = QFrame()
        top_bar.setObjectName("topRail")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(14, 8, 14, 8)
        top_layout.setSpacing(10)

        top_layout.addWidget(BrandMark())

        brand_col = QVBoxLayout()
        brand_col.setContentsMargins(0, 0, 0, 0)
        brand_col.setSpacing(0)
        lbl_brand_title = QLabel("Mini Keyboard")
        lbl_brand_title.setObjectName("brandTitle")
        brand_col.addWidget(lbl_brand_title)
        lbl_brand_sub = self.lbl_brand_sub = QLabel(f"{APP_VERSION} · " + tr_en("Yapılandırıcı", "Configurator"))
        lbl_brand_sub.setObjectName("brandSub")
        brand_col.addWidget(lbl_brand_sub)
        top_layout.addLayout(brand_col)

        top_layout.addStretch()

        # Port pill: just an LED (connected/disconnected) + the port code
        # itself - no repeated "Bağlı" text next to it, no separate refresh
        # button; hovering the pill shows the full status as a tooltip.
        self.port_pill = QFrame()
        self.port_pill.setObjectName("portPill")
        port_pill_layout = QHBoxLayout(self.port_pill)
        port_pill_layout.setContentsMargins(12, 4, 14, 4)
        port_pill_layout.setSpacing(8)

        self.status_led = QLabel()
        self.status_led.setFixedSize(8, 8)
        port_pill_layout.addWidget(self.status_led)

        self.combo_ports = QComboBox()
        self.combo_ports.setObjectName("portCombo")
        self.combo_ports.setMinimumWidth(70)
        port_pill_layout.addWidget(self.combo_ports)

        top_layout.addWidget(self.port_pill)
        self._set_status_led("#FF6B6B")
        self._set_connection_status_text(tr_en("Bağlı Değil", "Disconnected"))

        self.btn_connect = QPushButton("⏻")
        self.btn_connect.setObjectName("iconBtn")
        self.btn_connect.setToolTip(tr_en("Bağlan / Bağlantıyı Kes", "Connect / Disconnect"))
        self.btn_connect.setFixedSize(30, 30)
        self.btn_connect.clicked.connect(self.toggle_connection)
        top_layout.addWidget(self.btn_connect)

        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setObjectName("iconBtn")
        self.btn_settings.setToolTip(tr_en("Program ayarları", "Program settings"))
        self.btn_settings.setFixedSize(34, 30)
        self.btn_settings.clicked.connect(self.toggle_settings_page)
        top_layout.addWidget(self.btn_settings)

        main_layout.addWidget(top_bar)

        # 2. Main Stacked Container
        self.main_stack = QStackedWidget()

        # PAGE 0: Keyboard Configurator Page
        self.page_keyboard = QWidget()
        page_kb_layout = QVBoxLayout(self.page_keyboard)
        page_kb_layout.setContentsMargins(0, 4, 0, 0)
        page_kb_layout.setSpacing(6)

        # No boxed background here on purpose - the approved mockup's
        # ".mode-rail" sits directly on the page with no frame or border,
        # just the pills themselves with gaps between them.
        mode_frame = QFrame()
        mode_frame.setStyleSheet("QFrame { background: transparent; border: none; }")
        self.mode_bar = QHBoxLayout(mode_frame)
        self.mode_bar.setContentsMargins(2, 2, 2, 6)
        self.mode_bar.setSpacing(10)

        self.mode_buttons = []
        page_kb_layout.addWidget(mode_frame)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(0)

        left_group = PanelFrame("Fiziksel Klavye (1 ➔ 9)", hint="3×3")
        self.left_group = left_group
        grid_container = QVBoxLayout()
        grid_container.setContentsMargins(8, 8, 8, 8)
        left_group.body_layout.addLayout(grid_container)

        self.grid_layout = QGridLayout()
        self.grid_layout.setSpacing(10)
        self.grid_buttons = [None] * 9

        for key_id in range(9):
            num_label = key_id + 1
            btn = KeycapButton(num_label)
            btn.setObjectName("gridKey")
            btn.setProperty("gridKey", True)
            btn.setFixedSize(88, 88)
            if key_id == 4:
                btn.set_home(True)
            btn.clicked.connect(lambda _, k=key_id: self.select_key(k))
            self.grid_buttons[key_id] = btn

            row = 2 - (key_id // 3)
            col = key_id % 3
            self.grid_layout.addWidget(btn, row, col)

        grid_container.addLayout(self.grid_layout)
        grid_container.addStretch()
        content_layout.addWidget(left_group, stretch=4)

        self.trace_connector = TraceConnector()
        content_layout.addWidget(self.trace_connector)

        self.right_group = PanelFrame(tr_en("Tuş Ayarları", "Key Settings"))
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(8, 8, 8, 8)
        self.right_group.body_layout.addLayout(right_layout)

        self.tabs = QTabWidget()
        self.tab_single = KeyConfigWidget("Tek Tıklama")
        self.tab_long = KeyConfigWidget("Uzun Basma")

        self.tabs.addTab(self.tab_single, "Tek Tıklama")
        self.tabs.addTab(self.tab_long, "Uzun Basma")

        right_layout.addWidget(self.tabs)
        
        btn_apply_layout = QHBoxLayout()
        btn_apply_layout.addStretch()
        self.btn_apply_key = QPushButton("⚡ Kaydet && Yükle")
        self.btn_apply_key.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F2A96C, stop:1 #D98A54);
                color: #20130A;
                font-size: 12px;
                font-weight: bold;
                padding: 5px 16px;
                border: 1px solid #F2A96C;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FFC084, stop:1 #F2A96C);
                border-color: #FFC084;
            }
        """)
        self.btn_apply_key.clicked.connect(self.save_and_upload_key_config)
        btn_apply_layout.addWidget(self.btn_apply_key)

        right_layout.addLayout(btn_apply_layout)
        content_layout.addWidget(self.right_group, stretch=6)

        page_kb_layout.addLayout(content_layout)
        self.main_stack.addWidget(self.page_keyboard)

        self.rebuild_mode_bar()

        # PAGE 1: Full-Screen Program Settings Page
        self.page_settings = self.create_full_settings_page()
        self.main_stack.addWidget(self.page_settings)

        main_layout.addWidget(self.main_stack)

    def switch_main_page(self, page_idx):
        if page_idx == 1:
            self.refresh_system_health()
        self.main_stack.setCurrentIndex(page_idx)
        self._set_widget_active(self.btn_settings, page_idx != 0)

    def toggle_settings_page(self):
        self.switch_main_page(0 if self.main_stack.currentIndex() == 1 else 1)

    def _set_widget_active(self, widget, is_active):
        """Toggles the "active" dynamic property used by the #iconBtn.active
        QSS rule, then forces Qt to re-evaluate the stylesheet for this
        widget - setProperty() alone doesn't repaint a style-sheet-driven
        appearance change."""
        widget.setProperty("active", is_active)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _set_status_led(self, color_hex, glow=True):
        self.status_led.setStyleSheet(f"border-radius: 4px; background-color: {color_hex};")
        if glow:
            effect = QGraphicsDropShadowEffect(self.status_led)
            effect.setColor(QColor(color_hex))
            effect.setBlurRadius(10)
            effect.setOffset(0, 0)
            self.status_led.setGraphicsEffect(effect)
        else:
            self.status_led.setGraphicsEffect(None)

    def _set_connection_status_text(self, text):
        """Full connection status (connected/connecting/disconnected, or a
        worker error message) lives in the port pill's tooltip - the pill
        itself only ever shows the LED and the port code."""
        self.port_pill.setToolTip(text)

    def rebuild_mode_bar(self):
        if not hasattr(self, "mode_bar") or self.mode_bar is None:
            return

        # Layout'taki HER oge temizlenir. Eskiden yalnizca dugmeler
        # kaldiriliyordu; sondaki addStretch() her yeniden kurulumda (dil,
        # mod sayisi, mod adi degisince) bir tane daha birikiyor ve haplar
        # kayiyordu. Silinen dugmeler de deleteLater calisana kadar ekranda
        # "hayalet" olarak kalabildigi icin hemen gizlenir.
        while self.mode_bar.count():
            item = self.mode_bar.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self.mode_buttons.clear()
        self.mode_extra_btns = []

        count = getattr(self, "mode_count", 3)

        for i in range(count):
            color = self.mode_colors[i] if i < len(self.mode_colors) else "#F2A96C"
            btn_mode = ModePill(self.get_mode_display_name(i), color)
            btn_mode.set_on(i == self.active_mode)
            btn_mode.clicked.connect(lambda _, m=i: self.switch_mode(m, sync_device=True, show_popup=True))
            self.mode_buttons.append(btn_mode)
            self.mode_bar.addWidget(btn_mode)

        # Dashed "add a mode" / "remove last mode" circles, matching the
        # mockup's ".mode-add" - the remove button is the app's own
        # addition (not in the mockup) since a mode count that can only
        # grow isn't useful.
        if count > 1:
            remove_btn = QPushButton("−")
            remove_btn.setObjectName("modeAddBtn")
            remove_btn.setFixedSize(30, 30)
            remove_btn.setToolTip(tr_en("Son modu kaldır", "Remove last mode"))
            remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            remove_btn.clicked.connect(lambda: self.on_mode_count_changed(count - 1))
            self.mode_bar.addWidget(remove_btn)
            self.mode_extra_btns.append(remove_btn)

        if count < 5:
            add_btn = QPushButton("+")
            add_btn.setObjectName("modeAddBtn")
            add_btn.setFixedSize(30, 30)
            add_btn.setToolTip(tr_en("Mod ekle", "Add mode"))
            add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            add_btn.clicked.connect(lambda: self.on_mode_count_changed(count + 1))
            self.mode_bar.addWidget(add_btn)
            self.mode_extra_btns.append(add_btn)

        self.mode_bar.addStretch()

        if self.active_mode >= count:
            self.active_mode = 0

    def create_full_settings_page(self):
        # Wrapped in a QScrollArea so this page's content can scroll instead
        # of forcing a tall minimum window size - without it, the window
        # couldn't be resized shorter than however much content this page
        # happened to have (and kept growing as settings groups were added).
        page = QWidget()
        page_outer_layout = QVBoxLayout(page)
        page_outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Viewport ve icerik de saydam olmali; yoksa sistem temasinin
        # (acik temada gri) zemini koyu kartlarin arkasinda gorunuyordu.
        scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget "
            "{ background: transparent; border: none; }"
        )
        page_outer_layout.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        t = TRANSLATIONS[getattr(self, "language", "TR")]

        self.btn_settings_back = QPushButton(f"← {t['nav_keyboard']}")
        self.btn_settings_back.setObjectName("settingsBack")
        self.btn_settings_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings_back.clicked.connect(lambda: self.switch_main_page(0))
        layout.addWidget(self.btn_settings_back, alignment=Qt.AlignmentFlag.AlignLeft)

        # Spec-grid of flat cards, matching the approved mockup's settings
        # screen exactly: no big page title, just the back link then a 2-col
        # grid of cards (language, mode count, mode names/colors spanning
        # both columns, default port, general toggles, profile spanning
        # both columns).
        grid = QGridLayout()
        grid.setSpacing(14)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        # Language
        self.card_lang = SpecCard(t["lang_group"])
        self.seg_lang = SegmentedControl([("TR", "TR"), ("EN", "EN")])
        self.seg_lang.set_value(getattr(self, "language", "TR"), emit=False)
        self.seg_lang.currentChanged.connect(self.change_language)
        self.card_lang.body_layout.addWidget(self.seg_lang)
        grid.addWidget(self.card_lang, 0, 0)

        # Mode count
        self.card_mode_count = SpecCard(t["mode_count_group"])
        self.stepper_mode_count = Stepper(1, 5, getattr(self, "mode_count", 3))
        self.stepper_mode_count.valueChanged.connect(self.on_mode_count_changed)
        self.card_mode_count.body_layout.addWidget(self.stepper_mode_count)
        grid.addWidget(self.card_mode_count, 0, 1)

        # Mode names & colors (spans both columns)
        self.card_colors = SpecCard(t["mode_colors_group"])
        self.colors_layout = QVBoxLayout()
        self.colors_layout.setContentsMargins(0, 0, 0, 0)
        self.colors_layout.setSpacing(0)
        self.card_colors.body_layout.addLayout(self.colors_layout)
        self.rebuild_color_setting_rows()
        grid.addWidget(self.card_colors, 1, 0, 1, 2)

        # Default startup COM port
        self.card_port = SpecCard(t["port_group"])
        self.seg_board = SegmentedControl([("promicro", "Pro Micro / Micro"), ("nano", "Nano")])
        self.seg_board.set_value(getattr(self, "board", "promicro"), emit=False)
        self.seg_board.currentChanged.connect(self.change_board)
        self.card_port.body_layout.addWidget(self.seg_board)
        self.lbl_board_hint = QLabel(t["board_hint_" + getattr(self, "board", "promicro")])
        self.lbl_board_hint.setWordWrap(True)
        self.lbl_board_hint.setStyleSheet("color: #8B9289; font-size: 12px; padding: 4px 0 6px 0;")
        self.card_port.body_layout.addWidget(self.lbl_board_hint)
        self.combo_default_port = QComboBox()
        self.combo_default_port.setObjectName("settingsCombo")
        self.combo_default_port.addItem(t["auto_detect"], "")
        # Windows'ta COM1..COM20 diye sabit bir liste vardi; Linux'ta seri
        # port adlari sabit degil, takili aygita gore olusur. Bu yuzden o an
        # gercekten var olan portlar listelenir, kayitli port hala takili
        # degilse de secim kaybolmasin diye ayrica eklenir.
        _available = SerialWorker.list_available_ports()
        for _port in _available:
            self.combo_default_port.addItem(_port, _port)
        _saved = getattr(self, "default_com_port", "")
        if _saved and _saved not in _available:
            self.combo_default_port.addItem(f"{_saved} " + tr_en("(takılı değil)", "(not plugged in)"), _saved)
        if getattr(self, "default_com_port", ""):
            idx = self.combo_default_port.findData(self.default_com_port)
            if idx >= 0:
                self.combo_default_port.setCurrentIndex(idx)
        self.card_port.body_layout.addWidget(self.combo_default_port)
        grid.addWidget(self.card_port, 2, 0)

        # General behavior toggles
        self.card_gen = SpecCard(t["gen_group"])
        self.lbl_autostart, self.chk_autostart = self._build_switch_row(
            self.card_gen.body_layout, t["autostart"], self.is_autostart_enabled(),
            self.toggle_autostart, first=True)
        self.lbl_tray_close, self.chk_tray_close = self._build_switch_row(
            self.card_gen.body_layout, t["tray_close"], self.tray_close,
            lambda on: self._set_bool_setting("tray_close", on), first=False)
        self.lbl_popup_notify, self.chk_popup_notify = self._build_switch_row(
            self.card_gen.body_layout, t["popup_notify"], self.popup_notify,
            lambda on: self._set_bool_setting("popup_notify", on), first=False)
        grid.addWidget(self.card_gen, 2, 1)

        # Muzik / video tarayicisi: role bagli tuslarin hedefi.
        self.card_browsers = SpecCard(t["browser_group"])
        self.lbl_browser_hint = QLabel(t["browser_hint"])
        self.lbl_browser_hint.setWordWrap(True)
        self.lbl_browser_hint.setStyleSheet("color: #8B9289; font-size: 12px;")
        self.card_browsers.body_layout.addWidget(self.lbl_browser_hint)
        self.browser_combos = {}
        self.browser_labels = {}
        for attr, label_key in (("music_player", "browser_music"), ("video_player", "browser_video")):
            row = QHBoxLayout()
            row.setContentsMargins(0, 8, 0, 0)
            lbl = QLabel(t[label_key])
            lbl.setMinimumWidth(80)
            row.addWidget(lbl)
            combo = QComboBox()
            combo.setObjectName("settingsCombo")
            combo.currentIndexChanged.connect(
                lambda _i, a=attr, c=combo: self.set_role_player(a, c.currentData() or ""))
            row.addWidget(combo, stretch=1)
            if attr == "music_player":
                # Tek yenileme dugmesi ikisini birden doldurur.
                self.btn_refresh_browsers = QPushButton("🔄")
                self.btn_refresh_browsers.setObjectName("smallIconBtn")
                self.btn_refresh_browsers.setToolTip(t["browser_refresh_tip"])
                self.btn_refresh_browsers.setFixedSize(38, 34)
                self.btn_refresh_browsers.clicked.connect(lambda: self.refresh_browser_combos(force=True))
                row.addWidget(self.btn_refresh_browsers)
            else:
                row.addSpacing(38 + row.spacing())
            self.card_browsers.body_layout.addLayout(row)
            self.browser_combos[attr] = combo
            self.browser_labels[attr] = (lbl, label_key)
        self.refresh_browser_combos()
        player_cache().updated.connect(self.refresh_browser_combos)
        grid.addWidget(self.card_browsers, 3, 0, 1, 2)

        # Sistem durumu. Windows surumunde boyle bir kart yok; Linux'ta
        # programin calismasi birkac sistem iznine bagli (uinput aygiti,
        # seri porta erisim icin grup uyeligi) ve bunlar eksikken hata
        # sessizce "tus basildi ama hicbir sey olmadi" seklinde goruluyor.
        # Kart, kurulumun tamam olup olmadigini tek bakista gosterir.
        self.card_health = SpecCard(tr_en("Sistem Durumu", "System Status"))
        self.health_layout = QVBoxLayout()
        self.health_layout.setContentsMargins(0, 0, 0, 0)
        self.health_layout.setSpacing(0)
        self.card_health.body_layout.addLayout(self.health_layout)
        # Satirlar sayfa her acildiginda switch_main_page'de kurulur;
        # burada kurmak acilisi uinput aygitini bekleterek geciktiriyordu.
        grid.addWidget(self.card_health, 4, 0, 1, 2)

        # Profile import/export + settings save, all in one button row
        self.card_profile = SpecCard(t["profile_group"])
        profile_row = QHBoxLayout()
        profile_row.setSpacing(10)

        self.btn_export_profile = QPushButton(t["export_profile"])
        self.btn_export_profile.setObjectName("ghostBtn")
        self.btn_export_profile.clicked.connect(self.save_profile_file)
        profile_row.addWidget(self.btn_export_profile)

        self.btn_import_profile = QPushButton(t["import_profile"])
        self.btn_import_profile.setObjectName("ghostBtn")
        self.btn_import_profile.clicked.connect(self.load_profile_file)
        profile_row.addWidget(self.btn_import_profile)

        self.btn_save_app_settings = QPushButton(t["save_settings"])
        self.btn_save_app_settings.setObjectName("accentBtnSmall")
        self.btn_save_app_settings.clicked.connect(self.save_app_settings)
        profile_row.addWidget(self.btn_save_app_settings)

        profile_row.addStretch()
        self.card_profile.body_layout.addLayout(profile_row)
        grid.addWidget(self.card_profile, 5, 0, 1, 2)

        # Firmware yukleme. flash_arduino_code ve ArduinoFlasherWorker
        # vardi ama arayuzde onlari cagiran bir dugme yoktu.
        self.card_firmware = SpecCard(t["firmware_group"])
        fw_row = QHBoxLayout()
        fw_row.setSpacing(12)
        self.lbl_firmware_hint = QLabel(t["firmware_hint"])
        self.lbl_firmware_hint.setWordWrap(True)
        self.lbl_firmware_hint.setStyleSheet("color: #8B9289; font-size: 12px;")
        fw_row.addWidget(self.lbl_firmware_hint, stretch=1)
        self.btn_flash_firmware = QPushButton(t["firmware_btn"])
        self.btn_flash_firmware.setObjectName("ghostBtn")
        self.btn_flash_firmware.clicked.connect(self.flash_arduino_code)
        fw_row.addWidget(self.btn_flash_firmware)
        self.card_firmware.body_layout.addLayout(fw_row)
        grid.addWidget(self.card_firmware, 6, 0, 1, 2)

        layout.addStretch()
        return page

    def _build_switch_row(self, body_layout, text, checked, on_toggle, first):
        """One "label ... toggle" row inside a settings card, with a
        hairline divider above every row but the first - matches the
        mockup's ".check-row"."""
        if not first:
            body_layout.addWidget(row_divider())
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 9, 0, 9)
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #8B9289; font-size: 12.5px;")
        row_layout.addWidget(lbl, stretch=1)
        switch = ToggleSwitch()
        switch.setChecked(checked)
        if on_toggle:
            switch.stateChanged.connect(lambda st: on_toggle(bool(st)))
        row_layout.addWidget(switch)
        body_layout.addWidget(row)
        return lbl, switch

    def set_mode_name(self, mode_idx, name):
        name = name.strip()
        while len(self.mode_names) <= mode_idx:
            self.mode_names.append("")
        if self.mode_names[mode_idx] == name:
            return
        self.mode_names[mode_idx] = name
        self.save_local_user_config()
        self.rebuild_mode_bar()
        if mode_idx == self.active_mode:
            self.select_key_ui_only(self.selected_key_id)

    def rebuild_color_setting_rows(self):
        """One row per mode: color swatches plus its custom-name field,
        separated by a hairline divider - matches the mockup's
        ".mode-row" (swatch row + name input, no "MOD n:" label)."""
        if not hasattr(self, "colors_layout") or self.colors_layout is None:
            return

        while self.colors_layout.count():
            item = self.colors_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

        t = TRANSLATIONS.get(getattr(self, "language", "TR"), TRANSLATIONS["TR"])
        is_en = getattr(self, "language", "TR") == "EN"
        count = getattr(self, "mode_count", 3)
        names = getattr(self, "mode_names", ["", "", "", "", ""])

        self.mode_name_edits = []
        for m_idx in range(count):
            if m_idx > 0:
                self.colors_layout.addWidget(row_divider())

            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 9, 0, 9)
            row_layout.setSpacing(6)

            curr_hex = self.mode_colors[m_idx] if m_idx < len(self.mode_colors) else "#F2A96C"

            for preset in PRESET_COLORS:
                btn_color = QPushButton()
                btn_color.setToolTip(preset["name_en"] if is_en else preset["name_tr"])
                btn_color.setFixedSize(16, 16)
                btn_color.setCursor(Qt.CursorShape.PointingHandCursor)
                hex_c = preset["hex"]
                border = "#EDEAE0" if hex_c.lower() == curr_hex.lower() else "transparent"
                btn_color.setStyleSheet(
                    f"background-color: {hex_c}; border: 2px solid {border}; border-radius: 8px;"
                )
                btn_color.clicked.connect(lambda _, m=m_idx, c=hex_c: self.set_mode_color_hex(m, c))
                row_layout.addWidget(btn_color)

            edit = QLineEdit()
            edit.setObjectName("settingsInput")
            edit.setPlaceholderText(t["mode_name_placeholder"].format(m_idx + 1))
            edit.setText(names[m_idx] if m_idx < len(names) else "")
            edit.editingFinished.connect(lambda m=m_idx, e=edit: self.set_mode_name(m, e.text()))
            row_layout.addWidget(edit, stretch=1)
            self.mode_name_edits.append(edit)

            self.colors_layout.addWidget(row_widget)

    def on_mode_count_changed(self, new_count):
        if new_count and 1 <= new_count <= 5:
            # Once ekrandaki tus ayari kendi moduna yazilir. Etkin mod
            # kaldiriliyorsa rebuild_mode_bar onu sessizce MOD 1'e cekiyordu;
            # sag paneldeki alanlar ise kaldirilan modun tusunu gostermeye
            # devam ediyor ve bir sonraki tus seciminde MOD 1'in ayni
            # tusunun USTUNE yaziliyordu.
            self.save_current_key_widget_data()
            previous_mode = self.active_mode
            self.mode_count = new_count
            self.save_local_user_config()
            if hasattr(self, "stepper_mode_count"):
                self.stepper_mode_count.set_value(new_count, emit=False)
            self.rebuild_mode_bar()
            self.rebuild_color_setting_rows()
            if self.active_mode != previous_mode:
                self.switch_mode(self.active_mode, sync_device=False, show_popup=True,
                                 save_current=False)
            if self.is_connected:
                self.upload_config_to_device(show_dialog=False)

    def set_mode_color_hex(self, mode_idx, hex_color):
        if mode_idx < len(self.mode_colors):
            self.mode_colors[mode_idx] = hex_color
            self.save_local_user_config()
            self.rebuild_color_setting_rows()
            self.switch_mode(self.active_mode, sync_device=False, show_popup=False, save_current=False)

    def change_language(self, lang_code):
        self.language = lang_code
        set_language(lang_code)
        self.save_local_user_config()
        self.apply_language()

    def change_board(self, board):
        """Kart turu degisince bagli kart birakilir; Pro Micro'da otomatik
        arama, Nano'da yalnizca secili port denenir."""
        board = "nano" if board == "nano" else "promicro"
        if board == getattr(self, "board", "promicro"):
            return
        self.board = board
        SerialWorker.BOARD = board
        self.save_local_user_config()
        t = TRANSLATIONS.get(self.language, TRANSLATIONS["TR"])
        self.lbl_board_hint.setText(t["board_hint_" + board])
        if self.is_connected:
            self.worker.disconnect_port()
        port = self.default_com_port if board == "nano" else ""
        QTimer.singleShot(300, lambda: self.worker.connect_port(port))
        self.refresh_system_health()

    def _set_bool_setting(self, name, value):
        """Eskiden bu iki anahtar hicbir yere bagli degildi: ne okunuyor ne
        kaydediliyordu, her acilista yine "acik" geliyordu."""
        setattr(self, name, bool(value))
        self.save_local_user_config()

    def refresh_browser_combos(self, force=False):
        """Ayarlar'daki muzik/video tarayicisi listeleri: hazir tarayicilar,
        o an acik SMTC oynaticilari ve (artik acik degilse) kayitli secim."""
        if not hasattr(self, "browser_combos"):
            return
        t = TRANSLATIONS.get(getattr(self, "language", "TR"), TRANSLATIONS["TR"])
        players = list_players(force=force)
        for attr, combo in self.browser_combos.items():
            current = getattr(self, attr, "")
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(t["browser_none"], "")
            for key, name in BROWSER_PRESETS:
                combo.addItem(name, key)
            for key, display in players:
                if combo.findData(key) < 0:
                    combo.addItem(f"▶ {display} ({t['browser_open']})", key)
            if current and combo.findData(current) < 0:
                combo.addItem(player_display_name(current), current)
            combo.setCurrentIndex(max(0, combo.findData(current)))
            combo.blockSignals(False)

    def set_role_player(self, attr, value):
        """Muzik/video tarayicisini degistirir; role bagli tuslar hemen yeni
        hedefe gider (tus kayitlarinda yalnizca rol adi durur)."""
        if getattr(self, attr, "") == value:
            return
        setattr(self, attr, value)
        set_role_targets(getattr(self, "music_player", ""), getattr(self, "video_player", ""))
        self.save_local_user_config()
        for tab in (getattr(self, "tab_single", None), getattr(self, "tab_long", None)):
            if tab is not None:
                tab.refresh_players()
        # Bu metot listenin kendi sinyalinden cagriliyor; liste olay bitince yeniden kurulur.
        QTimer.singleShot(0, self.refresh_browser_combos)

    def refresh_system_health(self):
        """Sistem durumu kartinin satirlarini yeniden kurar."""
        while self.health_layout.count():
            item = self.health_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.deleteLater()

        rows = []

        # Linux'taki "/dev/uinput erisimi" satirinin karsiligi: Windows'ta
        # sanal tuslar (SendInput) yonetici olarak calisan bir pencereye
        # yalnizca Mini Keyboard da yonetici olarak calisiyorsa ulasir (UIPI).
        try:
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            is_admin = False
        rows.append((
            tr_en("Sanal klavye (SendInput)", "Virtual keyboard (SendInput)"),
            True,
            tr_en("hazır · yönetici", "ready · administrator") if is_admin else
            tr_en("hazır · yönetici penceresine tuş gönderemez", "ready · cannot send keys to admin windows"),
        ))

        # Yalnizca Mini Keyboard karti: tum seri portlar (baska bir
        # Arduino, mouse dongle'i) "hazir" diye listelenmez.
        worker = getattr(self, "worker", None)
        candidates = SerialWorker.candidate_ports()
        if worker is not None and worker.current_port:
            rows.append((tr_en("Mini Keyboard kartı", "Mini Keyboard board"), True, tr_en("bağlı", "connected") + f" · {worker.current_port}"))
        elif getattr(self, "board", "promicro") == "nano" and not getattr(self, "default_com_port", ""):
            rows.append((tr_en("Mini Keyboard kartı", "Mini Keyboard board"), False,
                         TRANSLATIONS.get(self.language, TRANSLATIONS["TR"])["nano_pick_port"]))
        elif not candidates:
            rows.append((tr_en("Mini Keyboard kartı", "Mini Keyboard board"), False, tr_en("bulunamadı (kart takılı mı?)", "not found (is it plugged in?)")))
        else:
            rows.append((tr_en("Mini Keyboard kartı", "Mini Keyboard board"), False, f"{candidates[0]} " + tr_en("takılı, bağlı değil", "plugged in, not connected")))

        has_smtc = smtc_available()
        rows.append((
            tr_en("Oynatıcı hedefleme (SMTC)", "Player targeting (SMTC)"),
            has_smtc,
            tr_en("hazır", "ready") if has_smtc else
            tr_en("smtc.ps1 bulunamadı - medya tuşları sistem geneli çalışır", "smtc.ps1 not found - media keys work system-wide"),
        ))

        for idx, (label, ok, detail) in enumerate(rows):
            if idx:
                self.health_layout.addWidget(row_divider())
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 8, 0, 8)
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #8B9289; font-size: 12.5px;")
            row_layout.addWidget(lbl, stretch=1)
            state = QLabel(("● " if ok else "▲ ") + detail)
            state.setStyleSheet(
                f"color: {'#8CFFC7' if ok else '#FFB454'}; font-size: 11.5px; font-weight: 600;"
            )
            state.setWordWrap(True)
            row_layout.addWidget(state, stretch=2)
            self.health_layout.addWidget(row)

    def apply_language(self):
        t = TRANSLATIONS.get(self.language, TRANSLATIONS["TR"])
        if hasattr(self, "btn_settings_back"):
            self.btn_settings_back.setText(f"← {t['nav_keyboard']}")
        if hasattr(self, "card_lang"):
            self.card_lang.setTitle(t["lang_group"])
            self.card_mode_count.setTitle(t["mode_count_group"])
            self.card_colors.setTitle(t["mode_colors_group"])
            self.card_port.setTitle(t["port_group"])
            self.lbl_board_hint.setText(t["board_hint_" + getattr(self, "board", "promicro")])
            self.card_gen.setTitle(t["gen_group"])
            self.lbl_autostart.setText(t["autostart"])
            self.lbl_tray_close.setText(t["tray_close"])
            self.lbl_popup_notify.setText(t["popup_notify"])
            self.card_browsers.setTitle(t["browser_group"])
            self.lbl_browser_hint.setText(t["browser_hint"])
            for lbl, key in self.browser_labels.values():
                lbl.setText(t[key])
            self.btn_refresh_browsers.setToolTip(t["browser_refresh_tip"])
            self.refresh_browser_combos()
            self.card_profile.setTitle(t["profile_group"])
            self.btn_export_profile.setText(t["export_profile"])
            self.btn_import_profile.setText(t["import_profile"])
            self.btn_save_app_settings.setText(t["save_settings"])
            self.card_firmware.setTitle(t["firmware_group"])
            self.lbl_firmware_hint.setText(t["firmware_hint"])
            self.btn_flash_firmware.setText(t["firmware_btn"])

        if hasattr(self, "lbl_brand_sub"):
            self.lbl_brand_sub.setText(f"{APP_VERSION} · " + tr_en("Yapılandırıcı", "Configurator"))
            self.btn_settings.setToolTip(tr_en("Program ayarları", "Program settings"))
        if hasattr(self, "tray_show_action"):
            self.tray_show_action.setText(tr_en("📌 Göster / Yapılandır", "📌 Show / Configure"))
            self.tray_quit_action.setText(tr_en("❌ Tamamen Çıkış Yap", "❌ Quit"))
        if hasattr(self, "card_health"):
            self.card_health.setTitle(tr_en("Sistem Durumu", "System Status"))
            self.refresh_system_health()
        if hasattr(self, "left_group"):
            self.left_group.setTitle(t["phys_keyboard"])
        self.btn_apply_key.setText(t["save_upload"])
        self.tabs.setTabText(0, t["tab_single"])
        self.tabs.setTabText(1, t["tab_long"])
        
        self.rebuild_mode_bar()
        self.rebuild_color_setting_rows()
        if hasattr(self, "worker"):
            self.refresh_ports()   # "Otomatik" / "Auto"

        if hasattr(self, "tab_single"):
            self.tab_single.retranslate_ui(self.language)
        if hasattr(self, "tab_long"):
            self.tab_long.retranslate_ui(self.language)

        if self.is_connected:
            self._set_connection_status_text(t["connected"].format(self.worker.current_port))
            self.btn_connect.setToolTip(t["disconnect"])
        else:
            self._set_connection_status_text(t["disconnected"])
            self.btn_connect.setToolTip(t["connect"])

        self.select_key_ui_only(self.selected_key_id)
        self.update_grid_button_labels()

    def save_app_settings(self):
        self.default_com_port = self.combo_default_port.currentData() or ""
        self.save_local_user_config()
        t = TRANSLATIONS.get(self.language, TRANSLATIONS["TR"])
        QMessageBox.information(self, t["save_success_title"], t["settings_saved"])

    def is_autostart_enabled(self):
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_READ
            )
            value, _ = winreg.QueryValueEx(key, "MiniKeyboardConfigurator")
            winreg.CloseKey(key)
            return bool(value)
        except Exception:
            return False

    def toggle_autostart(self, state):
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE
            )
            if state:
                pythonw_exe = sys.executable.replace("python.exe", "pythonw.exe")
                script_path = os.path.abspath(sys.argv[0])
                cmd = f'"{pythonw_exe}" "{script_path}"'
                winreg.SetValueEx(key, "MiniKeyboardConfigurator", 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, "MiniKeyboardConfigurator")
                except Exception:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            QMessageBox.warning(self, tr_en("Hata", "Error"), tr_en("Başlangıç ayarı değiştirilemedi: ", "Could not change the startup setting: ") + str(e))

    def switch_mode(self, mode_idx, sync_device=True, show_popup=True, save_current=True):
        if save_current:
            self.save_current_key_widget_data()
        # Etkin mod diske yazilmiyor: program her acilista MOD 1 ile
        # basliyor, yani kaydedilen deger hic okunmuyordu - buna ragmen her
        # mod degisiminde tum ayar dosyasi fsync ile yeniden yaziliyordu.
        self.active_mode = mode_idx

        for idx, btn in enumerate(self.mode_buttons):
            color = self.mode_colors[idx] if idx < len(self.mode_colors) else "#F2A96C"
            btn.set_color(color)
            btn.set_on(idx == mode_idx)

        if hasattr(self, "trace_connector"):
            active_color = self.mode_colors[mode_idx] if mode_idx < len(self.mode_colors) else "#D98A54"
            self.trace_connector.set_color(active_color)

        self.select_key_ui_only(self.selected_key_id)
        self.update_grid_button_labels()

        if show_popup:
            self.show_multi_monitor_popups(mode_idx)

        if sync_device and self.is_connected:
            self.upload_config_to_device(show_dialog=False)

    def on_hardware_mode_changed(self, new_mode):
        self.switch_mode(new_mode, sync_device=True, show_popup=True)

    def refresh_ports(self):
        """Port listesi: en ustte "Otomatik", sonra takili portlar.

        Eskiden sabit bir sira (once /dev/ttyACM0) mouse dongle'ini, kart
        bulunamayinca da listedeki ilk portu (ornegin baska bir kartin /dev/ttyUSB0'i)
        seciyordu. Artik bagliysa gercek port, degilse "Otomatik" secili
        durur; otomatik mod her adayi PING ile dogrular.
        """
        t = TRANSLATIONS.get(getattr(self, "language", "TR"), TRANSLATIONS["TR"])
        self.combo_ports.blockSignals(True)
        self.combo_ports.clear()
        self.combo_ports.addItem(t["auto_detect"], "")
        for port in SerialWorker.list_available_ports():
            self.combo_ports.addItem(port, port)
        idx = self.combo_ports.findData(self.worker.current_port) if self.worker.current_port else 0
        self.combo_ports.setCurrentIndex(max(idx, 0))
        self.combo_ports.blockSignals(False)

    def auto_connect_default_port(self):
        if getattr(self, "default_com_port", ""):
            idx = self.combo_ports.findData(self.default_com_port)
            if idx > 0:
                self.combo_ports.setCurrentIndex(idx)
                self.toggle_connection()
                return
        # Kayitli port yoksa (ya da takili degilse) combo'daki ilk porta degil,
        # otomatik aramaya birak: worker her adayi PING ile dogrular.
        if not self.is_connected:
            self.worker.connect_port("")

    def toggle_connection(self):
        t = TRANSLATIONS.get(self.language, TRANSLATIONS["TR"])
        if self.is_connected:
            self.worker.disconnect_port()
        else:
            port = self.combo_ports.currentData() or ""
            self.btn_connect.setEnabled(False)
            self._set_connection_status_text(t["connecting"].format(port or t["auto_detect"]))
            self._set_status_led("#FFB454")
            self.worker.connect_port(port)

    def on_connected_signal(self, success, message):
        t = TRANSLATIONS.get(self.language, TRANSLATIONS["TR"])
        self.btn_connect.setEnabled(True)
        self.is_connected = success
        # Yeni (ya da kopmus) baglantida kartin hangi ayari tasidigi bilinmez.
        self._device_state.clear()
        if success:
            # Port listesi yalnizca acilista doluyordu; kart sonradan (baska
            # bir ttyACM numarasiyla) takildiysa combo eski listeyi gosteriyordu.
            self.refresh_ports()
            self._set_connection_status_text(t["connected"].format(self.worker.current_port))
            self._set_status_led("#8CFFC7")
            self.btn_connect.setToolTip(t["disconnect"])
            self._set_widget_active(self.btn_connect, True)
            self.upload_config_to_device(show_dialog=False)
        else:
            # "X bir Mini Keyboard degil" / "bulunamadi" gibi nedenleri yut-
            # madan goster; siradan kopma mesajinda ceviriyi kullan.
            if message and message != DISCONNECTED:
                self._set_connection_status_text(message)
            else:
                self._set_connection_status_text(t["disconnected"])
            self._set_status_led("#FF6B6B")
            self.btn_connect.setToolTip(t["connect"])
            self._set_widget_active(self.btn_connect, False)

    def on_worker_status(self, msg):
        self._set_connection_status_text(tr_en("Durum: ", "Status: ") + msg)

    def flash_arduino_code(self):
        t = TRANSLATIONS.get(self.language, TRANSLATIONS["TR"])
        # Combo'daki metne degil, gercekten bagli olunan (ya da USB kimligine
        # gore en olasi) porta yuklenir; yanlis porta (mouse dongle'i,
        # baska bir kart) firmware yazmaya calismak tehlikeli.
        # Nano'da tahmin yok: baska bir Arduino'ya (ayni kopru yongasi)
        # firmware yazilabilirdi.
        if getattr(self, "board", "promicro") == "nano":
            port = self.worker.current_port or getattr(self, "default_com_port", "")
        else:
            port = self.worker.current_port or SerialWorker.guess_device_port()
        if not port:
            QMessageBox.warning(self, tr_en("Hata", "Error"), tr_en("Mini Keyboard kartı bulunamadı (takılı mı?).", "Mini Keyboard board not found (is it plugged in?)."))
            return
        if QMessageBox.question(self, t["firmware_group"], f"{t['firmware_confirm']}\n\nPort: {port}") \
                != QMessageBox.StandardButton.Yes:
            return

        self.worker.disconnect_port_synchronous()
        QApplication.processEvents()

        sketch_path = os.path.join(project_root, "arduino", "mini_keyboard")

        self.progress_dialog = QProgressDialog(tr_en("Firmware derleniyor...", "Compiling firmware..."),
                                               tr_en("İptal", "Cancel"), 0, 0, self)
        self.progress_dialog.setWindowTitle(t["firmware_group"])
        self.progress_dialog.setCancelButton(None)
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.show()

        self.flasher_worker = ArduinoFlasherWorker(port, sketch_path, board=getattr(self, "board", "promicro"))
        self.flasher_worker.progress_signal.connect(self.on_flasher_progress)
        self.flasher_worker.finished_signal.connect(self.on_flasher_finished)
        self.flasher_worker.start()

    def on_flasher_progress(self, msg):
        if self.progress_dialog:
            self.progress_dialog.setLabelText(msg)

    def on_flasher_finished(self, success, message):
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
        # disconnect_port_synchronous otomatik yeniden baglanmayi kapatiyor;
        # eskiden yukleme bittikten sonra program karta hic geri baglanmiyordu.
        # Kart yeniden tanitilana kadar biraz beklenir.
        QTimer.singleShot(1500, lambda: self.worker.connect_port(""))

        if success:
            QMessageBox.information(self, tr_en("Başarılı", "Success"), message)
        else:
            QMessageBox.critical(self, tr_en("Yükleme Hatası", "Upload Error"), message)

    def launch_app_path(self, path, run_as_admin=False):
        if not path:
            return
        path = fix_drive_relative_path(path)
        norm_path = os.path.normpath(path)

        try:
            existing_hwnd = find_running_app_window(norm_path)
        except Exception:
            existing_hwnd = None
        if existing_hwnd:
            activate_window(existing_hwnd)
            return

        if run_as_admin:
            # If a "MiniKeyboard_<AppName>" Scheduled Task was set up for
            # this app (Run With Highest Privileges), use it - Task
            # Scheduler can launch an already-admin-group user's task
            # elevated without a UAC prompt. Otherwise fall back to
            # ShellExecuteW's "runas" verb, which still elevates but shows
            # the standard UAC confirmation each time (that dialog is a
            # deliberate OS security boundary - it shouldn't and can't be
            # auto-clicked by an application).
            task_name = scheduled_task_name_for_app(norm_path)
            if task_name and scheduled_task_exists(task_name) and run_scheduled_task(task_name):
                return
            target = norm_path if os.path.exists(norm_path) else path
            work_dir = os.path.dirname(target) if os.path.exists(target) else None
            try:
                ctypes.windll.shell32.ShellExecuteW(None, "runas", target, None, work_dir, 1)
            except Exception:
                pass
            return
        try:
            if os.path.exists(norm_path):
                os.startfile(norm_path)
            else:
                subprocess.Popen(f'"{norm_path}"', shell=True)
        except Exception:
            try:
                os.startfile(path)
            except Exception:
                pass

    def launch_multi_app(self, paths, use_delay=False, delay_sec=1.0, run_as_admin=False):
        valid_paths = [p.strip() for p in paths if p and p.strip()]
        if not valid_paths:
            return

        if not use_delay:
            for p in valid_paths:
                self.launch_app_path(p, run_as_admin=run_as_admin)
        else:
            delay_ms = int(max(0.1, delay_sec) * 1000)
            for idx, p in enumerate(valid_paths):
                def make_launcher(target_p):
                    return lambda: self.launch_app_path(target_p, run_as_admin=run_as_admin)
                if idx == 0:
                    self.launch_app_path(p, run_as_admin=run_as_admin)
                else:
                    QTimer.singleShot(idx * delay_ms, make_launcher(p))

    def execute_action_dict(self, act_dict):
        if not act_dict:
            return
        act_type = act_dict.get("type")
        if act_type == "shortcut":
            run_in_background(trigger_shortcut, act_dict.get("mods", 0), act_dict.get("key", 0))
        elif act_type == "text":
            run_in_background(trigger_text, act_dict.get("text", ""), mode=act_dict.get("mode", "type"))
        elif act_type == "media":
            code, player = media_action_target(act_dict)
            run_in_background(trigger_media, code, player=player)
        elif act_type == "mode_switch":
            self.switch_mode((self.active_mode + 1) % self.mode_count, sync_device=True, show_popup=True)
        elif act_type == "app":
            paths = act_dict.get("paths", [])
            if not paths and act_dict.get("path"):
                paths = [act_dict.get("path")]
            run_as_admin = act_dict.get("run_as_admin", False)
            if len(paths) > 1:
                self.launch_multi_app(
                    paths,
                    use_delay=act_dict.get("use_delay", False),
                    delay_sec=act_dict.get("delay_sec", 1.0),
                    run_as_admin=run_as_admin
                )
            elif len(paths) == 1:
                self.launch_app_path(paths[0], run_as_admin=run_as_admin)

    def flash_key(self, btn, mode_color, duration_ms=150):
        """Light up key using Qt property — reliably overrides parent stylesheet.

        duration_ms is only a *minimum* visible time (so a very quick tap
        still flashes long enough to see) - it does not force the flash off
        while the physical key is still held. See _on_min_flash_elapsed."""
        if not btn:
            return
        if hasattr(btn, "_release_timer") and btn._release_timer:
            try:
                btn._release_timer.stop()
                btn._release_timer.deleteLater()
            except Exception:
                pass
            btn._release_timer = None

        btn._physically_held = True

        # Store the mode color on the button so we can rebuild the stylesheet
        btn._flash_color = mode_color

        # Rewriting the window-wide stylesheet makes Qt reparse it and
        # re-polish every descendant widget, so only do it when the flash
        # color actually differs from the rule already installed - otherwise
        # every keypress would pay that whole-tree cost for an identical sheet.
        if getattr(self, "_flash_rule_color", None) != mode_color:
            self._flash_rule_color = mode_color
            new_rule = (
                f'QPushButton#gridKey[flashing="true"] {{\n'
                f'    border-top: 2px solid {mode_color};\n'
                f'    border-left: 2px solid {mode_color};\n'
                f'    border-right: 2px solid {mode_color};\n'
                f'    border-bottom: 2px solid {mode_color};\n'
                f'    background-color: #0E1411;\n'
                f'    color: {mode_color};\n'
                f'    margin: 0px;\n'
                f'    padding: 0px;\n'
                f'}}'
            )
            self.setStyleSheet(_FLASH_RULE_RE.sub(new_rule, self.styleSheet()))

        btn.setProperty("flashing", "true")
        btn.style().unpolish(btn)
        btn.style().polish(btn)
        btn.repaint()

        if duration_ms > 0:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._on_min_flash_elapsed(btn))
            # Zamanlayici pencerenin cocugu; silinmezse her tus basisinda
            # bir tane daha birikiyordu.
            timer.timeout.connect(timer.deleteLater)
            timer.start(duration_ms)
            btn._release_timer = timer

    def pulse_long_press(self, btn):
        """Extra gold glow cue for a recognized long-press. Unlike a quick
        flash, this stays on for as long as the key remains physically held
        - the normal release flow (animate_release, triggered by the raw_up
        serial event) is what restores the regular color once it's let go."""
        if not btn:
            return
        effect = btn.graphicsEffect()
        if not isinstance(effect, QGraphicsDropShadowEffect):
            return

        pulse_color = QColor("#FFB454")
        pulse_color.setAlpha(235)
        effect.setColor(pulse_color)

        anim = QPropertyAnimation(effect, b"blurRadius", self)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(effect.blurRadius())
        anim.setKeyValueAt(0.5, 58)
        anim.setEndValue(44)
        # Bitince kendini siler; yoksa her uzun basista pencereye bir
        # animasyon nesnesi daha eklenip kaliyordu.
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_min_flash_elapsed(self, btn):
        """Minimum flash-visible time is up. If the key was released in the
        meantime, turn the flash off now. If it's still physically held,
        leave it flashing - the eventual raw_up event will turn it off."""
        if not btn:
            return
        btn._release_timer = None
        if not getattr(btn, "_physically_held", False):
            self.release_key(btn)

    def release_key(self, btn):
        """Turn off key flash animation."""
        if not btn:
            return
        btn._physically_held = False
        if hasattr(btn, "_release_timer") and btn._release_timer:
            try:
                btn._release_timer.stop()
                btn._release_timer.deleteLater()
            except Exception:
                pass
            btn._release_timer = None
        btn.setProperty("flashing", "false")
        btn.style().unpolish(btn)
        btn.style().polish(btn)
        btn.repaint()

    def on_physical_key_press(self, key_id, press_type="down"):
        if 0 <= key_id < 9 and self.grid_buttons[key_id]:
            btn = self.grid_buttons[key_id]
            mode_color = self.mode_colors[self.active_mode] if self.active_mode < len(self.mode_colors) else "#F2A96C"

            if press_type == "raw_down":
                # Light up immediately, stay lit until raw_up, minimum 150ms so it's always visible
                self.flash_key(btn, mode_color, duration_ms=150)
                btn.animate_press()
                return

            if press_type in ["raw_up", "up", "release"]:
                btn._physically_held = False
                # Only turn off now if the 150ms minimum-visible time has
                # already elapsed (timer is done). If it's still running,
                # _on_min_flash_elapsed will see _physically_held=False and
                # turn it off itself once that minimum time is up.
                if not (hasattr(btn, "_release_timer") and btn._release_timer):
                    self.release_key(btn)
                btn.animate_release()
                return

            kdata = self.modes[self.active_mode][key_id]
            if press_type == "long":
                self.pulse_long_press(btn)
                self.execute_action_dict(kdata.get("long", {}))
            else:
                self.execute_action_dict(kdata.get("single", {}))



    def select_key(self, key_id):
        self.save_current_key_widget_data()
        self.select_key_ui_only(key_id)

    def select_key_ui_only(self, key_id):
        self.selected_key_id = key_id
        t = TRANSLATIONS.get(self.language, TRANSLATIONS["TR"])

        if 0 <= key_id < 9:
            num_label = key_id + 1
            mode_label = self.get_mode_display_name(self.active_mode)
            self.right_group.setTitle(t["key_settings_title"].format(mode_label, num_label, KEY_PINS[key_id]))

            for idx, btn in enumerate(self.grid_buttons):
                btn.set_selected(idx == key_id)

            kdata = self.config_data[key_id]
            self.tab_single.set_data(kdata.get("single", {}))
            self.tab_long.set_data(kdata.get("long", {}))
        else:
            mode_label = self.get_mode_display_name(self.active_mode)
            self.right_group.setTitle(t["select_key_prompt"].format(mode_label))
            for idx, btn in enumerate(self.grid_buttons):
                btn.set_selected(False)

    def save_current_key_widget_data(self):
        if 0 <= self.selected_key_id < 9:
            old_data = self.config_data[self.selected_key_id]
            new_single = self.tab_single.get_data()
            new_long = self.tab_long.get_data()
            self.sync_admin_launch_tasks(old_data.get("single", {}), new_single)
            self.sync_admin_launch_tasks(old_data.get("long", {}), new_long)
            self.config_data[self.selected_key_id] = {
                "id": self.selected_key_id,
                "single": new_single,
                "long": new_long
            }
            self.save_local_user_config()
            self.update_grid_button_labels()

    def sync_admin_launch_tasks(self, old_act, new_act):
        """Keeps the 'launch elevated without UAC' Scheduled Tasks in step
        with the 'Run as Administrator' checkbox: checking it (and saving)
        sets one up for that app's path, unchecking it (or changing the
        path, or removing the app) tears it back down - so an app reverts
        to needing the normal UAC prompt exactly as it did before."""
        old_paths = set()
        if old_act.get("type") == "app" and old_act.get("run_as_admin"):
            old_paths = {p.strip() for p in old_act.get("paths", []) if p and p.strip()}
        new_paths = set()
        if new_act.get("type") == "app" and new_act.get("run_as_admin"):
            new_paths = {p.strip() for p in new_act.get("paths", []) if p and p.strip()}

        removed = old_paths - new_paths
        added = new_paths - old_paths
        if not removed and not added:
            return

        for path in removed:
            delete_admin_launch_task(path)

        for path in added:
            QMessageBox.information(
                self, tr_en("Yönetici İzni Kurulumu", "Administrator Permission Setup"),
                tr_en(f"'{os.path.basename(path)}' artık onay penceresi (UAC) çıkmadan\n"
                      "yönetici olarak açılabilsin diye bir kerelik izin gerekiyor.\n\n"
                      "Şimdi açılacak onay penceresini kabul et.",
                      f"So that '{os.path.basename(path)}' can start as administrator without\n"
                      "a UAC prompt every time, a one-time permission is needed.\n\n"
                      "Accept the prompt that opens now.")
            )
            create_admin_launch_task(path)

    def save_and_upload_key_config(self):
        self.upload_config_to_device(show_dialog=True)

    def update_grid_button_labels(self):
        tag_names = {
            "none": tr_en("BOŞ", "EMPTY"), "shortcut": tr_en("KISAYOL", "SHORTCUT"), "text": tr_en("METİN", "TEXT"),
            "media": tr_en("MEDYA", "MEDIA"), "mode_switch": tr_en("MOD", "MODE"), "app": tr_en("UYGULAMA", "APP"),
        }
        mod_symbols = (("⌃", 1), ("⇧", 2), ("⌥", 4), ("⊞", 8))
        for i, kdata in enumerate(self.config_data):
            single_type = kdata.get("single", {}).get("type", "none")
            glyph = "—"
            icon_pixmap = None
            if single_type == "shortcut":
                sdata = kdata["single"]
                key_code = sdata.get("key", 0)
                mods = sdata.get("mods", 0)
                combo = "".join(sym for sym, bit in mod_symbols if mods & bit)
                # chr(VK) yalnizca harf/rakamda dogru; F1 (0x70) "P", sol ok
                # (0x25) "%" gorunuyordu. Kayit penceresindeki adla ayni.
                kname = vk_to_display_name(key_code) if key_code else "?"
                glyph = f"{combo}{kname.upper() if len(kname) == 1 else kname}"
            elif single_type == "text":
                glyph = "Aa"
            elif single_type == "media":
                # Her medya tusu ayni ⏯ ile gorunuyordu; koda gore ayir.
                # 7/8/9: eski profil surumlerinin kodlari.
                code = kdata["single"].get("code", 4)
                code = {7: 4, 8: 5, 9: 6}.get(code, code)
                glyph = MEDIA_GLYPHS.get(code, "⏯")
                # Rol hedefli tusta rolun simgesi + islem sembolu yan yana.
                role_glyph = {MUSIC_ROLE: "🎵", VIDEO_ROLE: "🎬"}.get(
                    media_action_target(kdata["single"])[1], "")
                glyph = f"{role_glyph}{glyph}"
            elif single_type == "mode_switch":
                glyph = "🔀"
            elif single_type == "app":
                glyph = "🎮"
                icon_pixmap = self.get_app_icon_pixmap(kdata["single"].get("path", ""))

            if self.grid_buttons[i]:
                btn = self.grid_buttons[i]
                btn.set_content(glyph, tag_names.get(single_type, tag_names["none"]), ACTION_TYPE_COLORS.get(single_type), icon_pixmap)
                self.apply_key_glow(btn, single_type)

    def get_app_icon_pixmap(self, path, size=26):
        """The real icon of the assigned .exe/.lnk, replacing the generic
        controller glyph on app-type keys - cached per path since extracting
        a shell icon touches disk/the shell icon cache."""
        path = fix_drive_relative_path(path)
        if not path or not os.path.isfile(path):
            return None
        if not hasattr(self, "_app_icon_cache"):
            self._app_icon_cache = {}
        cached = self._app_icon_cache.get(path)
        if cached is not None:
            return cached if cached is not False else None
        pixmap = None
        try:
            icon = QFileIconProvider().icon(QFileInfo(path))
            candidate = icon.pixmap(size, size)
            if not candidate.isNull():
                pixmap = candidate
        except Exception:
            pixmap = None
        self._app_icon_cache[path] = pixmap if pixmap is not None else False
        return pixmap

    def apply_key_glow(self, btn, action_type):
        """Soft, anti-aliased colored glow around a key (instead of a hard
        pixel-edged QSS border) hinting at its assigned action type."""
        color_hex = ACTION_TYPE_COLORS.get(action_type)
        btn._glow_color_hex = color_hex
        effect = btn.graphicsEffect()
        if not isinstance(effect, QGraphicsDropShadowEffect):
            effect = QGraphicsDropShadowEffect(btn)
            effect.setOffset(0, 0)
            btn.setGraphicsEffect(effect)
        if color_hex:
            glow_color = QColor(color_hex)
            glow_color.setAlpha(200)
            effect.setColor(glow_color)
            effect.setBlurRadius(24)
        else:
            effect.setColor(QColor(0, 0, 0, 0))
            effect.setBlurRadius(0)

    # Tek satirda en fazla kac tus: 9 tus tek satira sigdirilinca satir
    # kartin 512 baytlik tamponuna dayaniyordu (bkz. mini_keyboard.ino
    # RX_BUFFER_SIZE). Uce bolununce en kotu durum ~240 bayt.
    WIRE_KEYS_PER_LINE = 3

    def upload_config_to_device(self, show_dialog=True):
        self.save_current_key_widget_data()
        if self.is_connected:
            keys = compress_keys_for_wire(self.config_data)
            state = (self.mode_count, keys)
            if not show_dialog and self._device_state.get(self.active_mode) == state:
                # Kart bu modun tuslarini bu baglantida zaten aldi. Eskiden
                # her mod degisiminde (fiziksel mod tusu dahil) 9 tusun
                # tamami yeniden gonderiliyordu; artik yalnizca mod secilir.
                self.worker.send_cmd("SET_MODE", {"cmd": "SET_MODE", "mode": self.active_mode})
            else:
                for start in range(0, len(keys), self.WIRE_KEYS_PER_LINE):
                    cmd = "SET_CONFIG" if start == 0 else "SET_KEY"
                    self.worker.send_cmd(cmd, {
                        "cmd": cmd,
                        "mode": self.active_mode,
                        "modeCount": self.mode_count,
                        "keys": keys[start:start + self.WIRE_KEYS_PER_LINE],
                    })
                self._device_state[self.active_mode] = state
        
        if show_dialog:
            t = TRANSLATIONS.get(self.language, TRANSLATIONS["TR"])
            mode_label = self.get_mode_display_name(self.active_mode)
            QMessageBox.information(self, t["save_success_title"], t["save_success_msg"].format(mode_label))

    def on_command_response(self, action_name, resp_dict, msg):
        """Kartin reddettigi komutlar sessizce kaybolmasin."""
        status = (resp_dict or {}).get("status")
        if status in ("ERROR", "FAILED") or (msg and msg != "OK"):
            detail = (resp_dict or {}).get("message") or msg
            # Kartin elindeki ayar artik bilinmiyor; bir sonraki gonderim tam olsun.
            self._device_state.clear()
            _logger.warning("Kart komutu reddetti (%s): %s", action_name, detail)
            self._set_connection_status_text(f"Kart ayarı kaydedemedi: {detail}")
        # GET_CONFIG ile kartin ayari GERI OKUNMUYOR: karta giden bicim artik
        # eksiltili (metin 7 ASCII karakter, uygulama yolu hic gitmiyor), yani
        # geri okunan ayar yerel ayarin ustune yazilsa makro metinleri ve
        # uygulama yollari silinirdi. Dogru kaynak user_config.json.

    def save_profile_file(self):
        self.save_current_key_widget_data()
        file_path, _ = QFileDialog.getSaveFileName(self, tr_en("Profili Kaydet", "Save Profile"), "", tr_en("JSON Profil", "JSON Profile") + " (*.json)")
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump({
                        "active_mode": self.active_mode,
                        "mode_count": self.mode_count,
                        "mode_colors": self.mode_colors,
                        "mode_names": self.mode_names,
                        "music_player": self.music_player,
                        "video_player": self.video_player,
                        "modes": self.modes
                    }, f, indent=2, ensure_ascii=False)
                QMessageBox.information(self, tr_en("Başarılı", "Success"), tr_en("Profil kaydedildi.", "Profile saved."))
            except Exception as e:
                QMessageBox.critical(self, tr_en("Hata", "Error"), tr_en("Profil kaydedilemedi: ", "Could not save the profile: ") + str(e))

    def load_profile_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, tr_en("Profil Aç", "Open Profile"), "", tr_en("JSON Profil", "JSON Profile") + " (*.json)")
        if file_path:
            # Yukleme basarisiz olursa ekranda yapilan son degisiklik
            # kaybolmasin diye once mevcut profile yazilir.
            self.save_current_key_widget_data()
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "modes" in data:
                        self.mode_count = max(1, min(5, int(data.get("mode_count", self.mode_count))))
                        self.active_mode = int(data.get("active_mode", 0)) % self.mode_count
                        saved_colors = data.get("mode_colors", [])
                        if isinstance(saved_colors, list):
                            for idx, c in enumerate(saved_colors):
                                if idx < 5:
                                    self.mode_colors[idx] = c
                        saved_names = data.get("mode_names", [])
                        if isinstance(saved_names, list):
                            for idx, n in enumerate(saved_names):
                                if idx < 5 and isinstance(n, str):
                                    self.mode_names[idx] = n
                        self.modes = self._normalized_modes(data.get("modes"), self.modes)
                        if "music_player" in data or "video_player" in data:
                            self.set_role_player("music_player", str(data.get("music_player", "") or ""))
                            self.set_role_player("video_player", str(data.get("video_player", "") or ""))
                        self.save_local_user_config()
                        self.rebuild_mode_bar()
                        self.rebuild_color_setting_rows()
                        # save_current=False: ekrandaki alanlar ESKI profilin
                        # secili tusunu tutuyor; kaydedilseydi yeni yuklenen
                        # profilin ayni tusunun ustune yazilirdi.
                        self.switch_mode(self.active_mode, sync_device=True, show_popup=True,
                                         save_current=False)
                        QMessageBox.information(self, tr_en("Başarılı", "Success"), tr_en("Profil yüklendi.", "Profile loaded."))
                    elif isinstance(data, list) and len(data) == 9:
                        self.modes[0] = data
                        self.save_local_user_config()
                        self.switch_mode(0, sync_device=True, show_popup=True, save_current=False)
                        QMessageBox.information(self, tr_en("Başarılı", "Success"), tr_en("Profil Mod 1 olarak yüklendi.", "Profile loaded as Mode 1."))
                    else:
                        QMessageBox.warning(self, tr_en("Hata", "Error"), tr_en("Geçersiz profil formatı.", "Invalid profile format."))
            except Exception as e:
                QMessageBox.critical(self, tr_en("Hata", "Error"), tr_en("Profil okunamadı: ", "Could not read the profile: ") + str(e))


# Masaustu kisayolu ya da dis bir aracin "arayuzu goster" komutu, program zaten
# arka planda (tepside gizli) calisirken yeni bir kopya baslatmadan mevcut
# pencereyi one getirebilsin diye: yerel (127.0.0.1) UDP uzerinden tek-ornek
# sinyali.
_SHOW_UI_UDP_PORT = 49159
_SHOW_UI_MAGIC = b"MINIKB_SHOW_UI_SIGNAL"


def _notify_running_instance_to_show() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            sock.sendto(_SHOW_UI_MAGIC, ("127.0.0.1", _SHOW_UI_UDP_PORT))
        return True
    except Exception:
        return False


def _start_show_ui_listener(window) -> None:
    """Arka planda calisirken "--show" ile tekrar baslatilmaya calisildiginda
    (bkz. __main__ altindaki erken-cikis) gelen sinyali dinler ve pencereyi
    ana Qt thread'inde acar (window.show_requested sinyali uzerinden - Qt
    sinyalleri farkli thread'ler arasinda otomatik guvenli kuyruklanir)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.settimeout(30.0)
        sock.bind(("127.0.0.1", _SHOW_UI_UDP_PORT))
    except OSError:
        return

    def _dinle():
        while True:
            try:
                data, _addr = sock.recvfrom(1024)
                if _SHOW_UI_MAGIC in data:
                    window.show_requested.emit()
            except socket.timeout:
                continue
            except OSError:
                break

    threading.Thread(target=_dinle, daemon=True, name="MiniKB-ShowUI").start()


if __name__ == "__main__":
    # Tek ornek kilidi: named Win32 mutex atomiktir, ayni anda yalnizca bir
    # surec tutabilir (oturum acilisindaki otomatik kopya ile elle acilan
    # kopya yarissa bile). Eskiden ayrica kill_duplicate_python_instances()
    # komut satirinda proje klasoru gecen HER python surecini olduruyordu
    # (test betikleri, araclar dahil); mutex varken gereksiz ve tehlikeliydi,
    # 08.10'da kaldirildi.
    _SINGLE_INSTANCE_MUTEX_NAME = "Global\\MiniKeyboardConfigurator_SingleInstance"
    _single_instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
    _istenen_goster = "--show" in sys.argv
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        # Yalnizca pencere istendiyse (masaustu kisayolu --show verir)
        # calisan kopyaya haber verilir. Eskiden --show'suz ikinci bir
        # baslatma (oturum acilisi girdisi) da gizli calisan kopyanin
        # penceresini ekrana getiriyordu.
        if _istenen_goster:
            _notify_running_instance_to_show()
        sys.exit(0)

    app = QApplication(sys.argv)
    window = MainWindow()
    _start_show_ui_listener(window)
    # Kopya: thread okurken arayuz modlari degistirebilir.
    threading.Thread(target=kisayollari_onceden_coz, args=(json.loads(json.dumps(window.modes)),),
                     name="MiniKB-LnkIsit", daemon=True).start()
    # Kalici SMTC surecini onceden baslat: ilk medya tusu da ~250 ms beklemesin.
    threading.Thread(target=_smtc_sunucu.isit, name="MiniKB-SMTC-Isit", daemon=True).start()
    if _istenen_goster:
        window.show_window_from_tray()
    else:
        # Start hidden in the system tray - the configurator window only opens
        # when the user asks for it (tray icon click), not on every launch.
        window.tray_icon.showMessage(
            "Mini Keyboard",
            tr_en("Program arka planda çalışıyor. Yapılandırmak için sistem tepsisindeki simgeye tıklayın.",
                  "Running in the background. Click the tray icon to configure."),
            QSystemTrayIcon.MessageIcon.Information,
            3000
        )
    _exit_code = app.exec()
    _logger.info("Mini Keyboard temiz sekilde kapaniyor (exit_code=%s).", _exit_code)
    sys.exit(_exit_code)
