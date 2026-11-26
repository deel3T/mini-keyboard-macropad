"""Arayuzden tek tikla firmware derleme/yukleme (arduino-cli).

Linux surumuyle ayni isi yapar; platforma bagli iki fark var:

1. arduino-cli ve avrdude'un aranacagi yerler Windows'a gore (Arduino IDE
   2.x'in kendi kopyasi, %LOCALAPPDATA%\\Arduino15, PATH).
2. subprocess cagrilarinda CREATE_NO_WINDOW bayragi var: pythonw ile
   calisirken her alt surec icin bir konsol penceresi yanip sonmesin.

AVR cekirdegi ya da Keyboard kutuphanesi eksikse arduino-cli'nin ham hata
ciktisi yerine ne yapilmasi gerektigi soyleniyor.
"""

import glob
import os
import shutil
import subprocess
import tempfile
import time

from PyQt6.QtCore import QThread, pyqtSignal

from i18n import t

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0x08000000

FQBN = "arduino:avr:micro"
# Nano: yeni bootloader (Optiboot, 115200 baud) once denenir; senkron hatasi
# verirse eski bootloader (57600 baud, cogu CH340'li klon) ile tekrarlanir.
NANO_FQBNS = ("arduino:avr:nano:cpu=atmega328", "arduino:avr:nano:cpu=atmega328old")

_LOCAL_APP = os.environ.get("LOCALAPPDATA", "")

# ATmega32U4 onyukleyicilerinin (Caterina) USB kimlikleri. Arduino/Arduino.org
# kartlarinda onyukleyici PID'inde 0x8000 biti yoktur (Micro: 0x0037,
# sketch'i 0x8037); SparkFun Pro Micro'larda onyukleyici 9203/9205/9207'dir.
_ARDUINO_VIDS = (0x2341, 0x2A03)
_SPARKFUN_BOOTLOADERS = {(0x1B4F, 0x9203), (0x1B4F, 0x9205), (0x1B4F, 0x9207)}

MANUAL_RESET_SECONDS = 25
_MANUAL_RESET_HINT_TR = (
    "Kart önyükleme moduna kendiliğinden geçmedi.\n\n"
    "Pro Micro'da RST pinini GND'ye İKİ KEZ hızlıca değdirin\n"
    "(Micro'da reset düğmesine iki kez basın).\n\n"
    "{} saniye içinde kart görülünce yükleme kendiliğinden başlar..."
)
_MANUAL_RESET_HINT_EN = (
    "The board did not enter bootloader mode by itself.\n\n"
    "On a Pro Micro, quickly touch RST to GND TWICE\n"
    "(on a Micro, press the reset button twice).\n\n"
    "The upload starts as soon as the board appears ({} s left)..."
)


def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          creationflags=CREATE_NO_WINDOW)


def _is_bootloader(port):
    if port.vid is None or port.pid is None:
        return False
    if port.vid in _ARDUINO_VIDS:
        return not (port.pid & 0x8000)
    return (port.vid, port.pid) in _SPARKFUN_BOOTLOADERS


def find_bootloader_port():
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    for p in list_ports.comports():
        if _is_bootloader(p):
            return p.device
    return None


def find_avrdude():
    """arduino-cli'nin indirdigi avrdude (yoksa PATH'teki)."""
    pattern = os.path.join(_LOCAL_APP, "Arduino15", "packages", "arduino", "tools",
                           "avrdude", "*", "bin", "avrdude.exe")
    for path in sorted(glob.glob(pattern), reverse=True):
        conf = os.path.join(os.path.dirname(os.path.dirname(path)), "etc", "avrdude.conf")
        if os.path.isfile(path) and os.path.isfile(conf):
            return path, conf
    system = shutil.which("avrdude")
    if system:
        return system, None
    return None, None


class ArduinoFlasherWorker(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, port, sketch_path, board="promicro"):
        super().__init__()
        self.port = port
        self.sketch_path = sketch_path
        self.board = board

    @staticmethod
    def find_arduino_cli():
        candidates = [
            # Arduino IDE 2.x kendi kopyasini tasir; kullanici ayrica
            # arduino-cli kurmamis olabilir.
            os.path.join(_LOCAL_APP, r"Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"),
            os.path.join(_LOCAL_APP, r"Arduino15\arduino-cli.exe"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return shutil.which("arduino-cli")

    @staticmethod
    def avr_core_installed(cli_path):
        """arduino:avr cekirdegi kurulu mu?

        Kurulu degilse derleme "Platform 'arduino:avr' not found" diye
        patlar; bunu onceden bilip kullaniciya tek satirlik cozumu
        soyleyebilmek icin bakiliyor.
        """
        try:
            res = _run([cli_path, "core", "list"], timeout=20)
        except Exception:
            return True  # emin olunamiyorsa derlemeyi yine de dene
        return "arduino:avr" in (res.stdout or "")

    def run(self):
        cli_path = self.find_arduino_cli()
        if not cli_path:
            self.finished_signal.emit(False, t(
                "arduino-cli bulunamadı.\n\n"
                "Arduino IDE 2'yi kurun (kendi arduino-cli kopyasıyla gelir)\n"
                "ya da:\n"
                "    winget install ArduinoSA.CLI\n\n"
                "Kurduktan sonra AVR çekirdeğini de indirin:\n"
                "    arduino-cli core update-index\n"
                "    arduino-cli core install arduino:avr",
                "arduino-cli not found.\n\n"
                "Install Arduino IDE 2 (it ships its own arduino-cli)\n"
                "or:\n"
                "    winget install ArduinoSA.CLI\n\n"
                "Then install the AVR core:\n"
                "    arduino-cli core update-index\n"
                "    arduino-cli core install arduino:avr"
            ))
            return

        if not self.avr_core_installed(cli_path):
            self.finished_signal.emit(False, t(
                "Arduino AVR çekirdeği kurulu değil.\n\n"
                "Şu iki komutu çalıştırın:\n"
                "    arduino-cli core update-index\n"
                "    arduino-cli core install arduino:avr",
                "The Arduino AVR core is not installed.\n\n"
                "Run these two commands:\n"
                "    arduino-cli core update-index\n"
                "    arduino-cli core install arduino:avr"
            ))
            return

        build_dir = tempfile.mkdtemp(prefix="minikb-build-")
        try:
            self._compile_and_upload(cli_path, build_dir)
        finally:
            shutil.rmtree(build_dir, ignore_errors=True)

    def _compile_and_upload(self, cli_path, build_dir):
        if self.board == "nano":
            self._compile_and_upload_nano(cli_path, build_dir)
            return
        self.progress_signal.emit(t("Firmware derleniyor...", "Compiling firmware..."))
        compile_cmd = [cli_path, "compile", "--fqbn", FQBN, "--output-dir", build_dir, self.sketch_path]
        try:
            res = _run(compile_cmd, timeout=180)
            if res.returncode != 0:
                message = res.stderr or res.stdout
                if "Keyboard.h" in message:
                    # Keyboard, AVR cekirdeginden ayri dagitilan bir kutuphane;
                    # cekirdek kurulu olsa bile eksik olabiliyor.
                    message += t(
                        "\n\nArduino Keyboard kütüphanesi kurulu değil:\n"
                        "    arduino-cli lib install Keyboard",
                        "\n\nThe Arduino Keyboard library is not installed:\n"
                        "    arduino-cli lib install Keyboard"
                    )
                self.finished_signal.emit(False, t("Derleme hatası:\n", "Compile error:\n") + message)
                return
        except Exception as e:
            self.finished_signal.emit(False, t("Derleme aracı çalıştırılamadı: ", "Could not run the compiler: ") + str(e))
            return

        self.progress_signal.emit(t(f"Firmware karta yükleniyor ({self.port})...", f"Uploading firmware to the board ({self.port})..."))
        upload_cmd = [cli_path, "upload", "-p", self.port, "--fqbn", FQBN,
                      "--input-dir", build_dir, self.sketch_path]
        try:
            res = _run(upload_cmd, timeout=120)
        except Exception as e:
            self.finished_signal.emit(False, t("Yükleme hatası: ", "Upload error: ") + str(e))
            return
        if res.returncode == 0:
            self.finished_signal.emit(True, t("Firmware karta yüklendi! 🎉", "Firmware uploaded to the board! 🎉"))
            return

        message = res.stderr or res.stdout
        low = message.lower()
        if "access is denied" in low or "erişim engellendi" in low:
            message += t(
                "\n\nPort başka bir program tarafından kullanılıyor "
                "(Arduino IDE Seri Monitör, başka bir Mini Keyboard kopyası?). "
                "O programı kapatıp tekrar deneyin.",
                "\n\nThe port is used by another program "
                "(Arduino IDE Serial Monitor, another Mini Keyboard instance?). "
                "Close it and try again."
            )
            self.finished_signal.emit(False, t("Yükleme hatası:\n", "Upload error:\n") + message)
            return

        # Kart 1200 baud sinyaliyle sifirlandi ama onyukleyicide beklemeden
        # programa dondu. Eski firmware'de 0x0800'deki "bekle" isareti her
        # milisaniye degisen timer0_fract'in ustune dusuyordu; boyle bir
        # kart ancak elle sifirlanarak yuklenebilir.
        self._manual_reset_upload(os.path.join(build_dir, "mini_keyboard.ino.hex"), message)

    def _compile_and_upload_nano(self, cli_path, build_dir):
        """Nano: 1200 baud sifirlama ya da elle reset gerekmez; port acilinca
        kopru yongasinin DTR hatti karti bootloader'a sokar."""
        message = ""
        for idx, fqbn in enumerate(NANO_FQBNS):
            out_dir = os.path.join(build_dir, str(idx))
            self.progress_signal.emit(t("Firmware derleniyor (Nano)...", "Compiling firmware (Nano)..."))
            try:
                res = _run([cli_path, "compile", "--fqbn", fqbn, "--output-dir", out_dir, self.sketch_path], timeout=180)
            except Exception as e:
                self.finished_signal.emit(False, t("Derleme aracı çalıştırılamadı: ", "Could not run the compiler: ") + str(e))
                return
            if res.returncode != 0:
                self.finished_signal.emit(False, t("Derleme hatası:\n", "Compile error:\n") + (res.stderr or res.stdout))
                return
            label = t("yeni bootloader", "new bootloader") if idx == 0 else t("eski bootloader", "old bootloader")
            self.progress_signal.emit(t(f"Nano'ya yükleniyor ({self.port}, {label})...",
                                        f"Uploading to Nano ({self.port}, {label})..."))
            try:
                res = _run([cli_path, "upload", "-p", self.port, "--fqbn", fqbn,
                            "--input-dir", out_dir, self.sketch_path], timeout=90)
            except Exception as e:
                self.finished_signal.emit(False, t("Yükleme hatası: ", "Upload error: ") + str(e))
                return
            if res.returncode == 0:
                self.finished_signal.emit(True, t("Firmware karta yüklendi! 🎉", "Firmware uploaded to the board! 🎉"))
                return
            message = res.stderr or res.stdout
            low = message.lower()
            if "not in sync" not in low and "not responding" not in low and "timeout" not in low:
                break  # bootloader farki degil (port mesgul vb.): eskiyle denemek anlamsiz
        self.finished_signal.emit(False, t("Yükleme hatası:\n", "Upload error:\n") + message)

    def _manual_reset_upload(self, hex_path, first_error):
        avrdude, conf = find_avrdude()
        if not avrdude or not os.path.isfile(hex_path):
            self.finished_signal.emit(False, t("Yükleme hatası:\n", "Upload error:\n") + first_error)
            return
        deadline = time.time() + MANUAL_RESET_SECONDS
        port = None
        shown_left = None
        while time.time() < deadline:
            left = int(deadline - time.time())
            # Yalnizca geri sayim degisince: ayni metni saniyede 10 kez
            # gondermek ilerleme penceresini bosuna yeniden cizdirir.
            if left != shown_left:
                shown_left = left
                self.progress_signal.emit(t(_MANUAL_RESET_HINT_TR, _MANUAL_RESET_HINT_EN).format(left))
            port = find_bootloader_port()
            if port:
                break
            time.sleep(0.1)
        if not port:
            self.finished_signal.emit(False, t(
                "Kart önyükleme modunda görülmedi, firmware yüklenmedi.\n"
                "Karttaki eski firmware çalışmaya devam ediyor.\n\n"
                "RST-GND'yi iki kez, arka arkaya hızlıca değdirip tekrar deneyin.",
                "The board never appeared in bootloader mode; nothing was uploaded.\n"
                "The old firmware keeps running.\n\n"
                "Touch RST to GND twice in quick succession and try again."
            ))
            return
        self.progress_signal.emit(t(f"Önyükleyici bulundu ({port}), yazılıyor...", f"Bootloader found ({port}), writing..."))
        # Windows'ta onyukleyicinin COM portu surucu tarafindan hazir hale
        # getirilene kadar kisa bir sure gecer.
        time.sleep(0.5)
        cmd = [avrdude] + (["-C", conf] if conf else []) + [
            "-p", "atmega32u4", "-c", "avr109", "-P", port, "-b", "57600",
            "-D", "-U", f"flash:w:{hex_path}:i",
        ]
        try:
            res = _run(cmd, timeout=60)
        except Exception as e:
            self.finished_signal.emit(False, t("Yükleme hatası: ", "Upload error: ") + str(e))
            return
        if res.returncode == 0:
            self.finished_signal.emit(True, t("Firmware karta yüklendi! 🎉", "Firmware uploaded to the board! 🎉"))
        else:
            self.finished_signal.emit(False, t("Yükleme hatası:\n", "Upload error:\n") + (res.stderr or res.stdout))
