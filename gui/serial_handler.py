import json
import queue
import time
import serial
import serial.tools.list_ports
from PyQt6.QtCore import QThread, pyqtSignal

from i18n import t

# Siradan kopma: metin degil isaret; arayuz kendi cevirisini gosterir.
DISCONNECTED = "\x00disconnected"

class SerialWorker(QThread):
    connected_signal = pyqtSignal(bool, str)
    key_pressed_signal = pyqtSignal(int, str)
    mode_changed_signal = pyqtSignal(int)
    command_response_signal = pyqtSignal(str, dict, str)
    status_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.ser = None
        self.running = True
        self.cmd_queue = queue.Queue()
        self.baudrate = 115200
        self.current_port = None
        self.auto_reconnect = True
        self._rx_buffer = b""
        self.user_selected_port = None
        # PING'e cevap vermeyen portlar, port listesi degisene kadar tekrar
        # denenmez (dongle'a her 1.5 sn'de bir veri yazmamak icin).
        self._rejected_ports = set()
        self._rejected_signature = None

    # Bu firmware'in PING cevabinda kendini tanittigi ad (mini_keyboard.ino).
    DEVICE_NAME = "MacroPad3x3"

    # ATmega32U4 kartlarin (Arduino Micro/Leonardo, SparkFun ve klon Pro
    # Micro'lar) USB uretici kimlikleri. Klon Pro Micro'lar genellikle
    # "Arduino Micro" ya da "Leonardo" olarak yuklendigi icin 2341/2A03
    # kimligiyle, SparkFun bootloader'iyla yuklenenler 1B4F ile gorunur.
    ATMEGA32U4_VIDS = (0x2341, 0x2A03, 0x1B4F)

    # Kart turu: "promicro" (32U4, yerlesik USB) ya da "nano" (ATmega328P +
    # USB-seri kopru yongasi). Arayuz ayardan atar.
    BOARD = "promicro"
    # Nano'larda USB'yi kopru yongasi saglar: CH340 (klonlar), FTDI FT232R
    # (orijinal Nano), CP210x.
    BRIDGE_VIDS = (0x1A86, 0x0403, 0x10C4)

    # Eskiden burada sabit bir tercih sirasi vardi (COM13, COM14, ...).
    # COM numarasi USB yuvasina ve takilma sirasina gore degisir; ayrica
    # ayni makinede baska seri aygitlar (fare alicisi, baska bir Arduino)
    # olabilir. Artik port adina degil, USB kimligine ve cihazin PING
    # cevabina bakiliyor.

    @staticmethod
    def list_available_ports():
        """Takili seri portlar.

        Yalnizca bir USB aygitina ait portlar gosterilir: Windows'ta
        comports() Bluetooth uzerinden seri port (SPP) sanal COM'larini ve
        anakart UART'larini da dondurur, bunlar listede Arduino'yu bulmayi
        zorlastirir. Hicbiri yoksa filtre uygulanmadan tamami dondurulur,
        boylece filtre kimseyi kilitlemez.
        """
        ports = serial.tools.list_ports.comports()
        usb_ports = [p.device for p in ports if p.vid is not None]
        return usb_ports if usb_ports else [p.device for p in ports]

    @classmethod
    def candidate_ports(cls):
        """Mini Keyboard olabilecek portlar (otomatik arama bunlari dener).

        Port acilmaz, yalnizca USB bilgisine bakilir. Linux surumu
        ATmega32U4 kimligi olmayan ttyACM portlarini da (klon kart ihtimali)
        sona ekliyor, dongle'lari aciklamadaki "dongle/receiver" gibi
        kelimelerle eliyor. Windows ise CDC aygitlarinin hepsini "USB Seri
        Cihaz" diye adlandirdigi icin bu ayrim yapilamaz; kimligi farkli
        her porta PING yazmak dongle'a ya da baska bir karta veri gondermek
        olurdu. Bu yuzden Windows'ta yalnizca 32U4 kimlikli portlar denenir;
        farkli kimlikli bir klon kart arayuzden elle secilebilir.
        """
        vids = cls.BRIDGE_VIDS if cls.BOARD == "nano" else cls.ATMEGA32U4_VIDS
        return [p.device for p in serial.tools.list_ports.comports()
                if p.device and p.vid in vids]

    @classmethod
    def probe_ports(cls):
        """Otomatik aramada ACILIP PING'lenecek portlar.

        Nano'da bos: Nano'nun (ve her Uno/Nano klonunun) seri portu acilinca
        DTR hatti karti sifirlar. Otomatik arama baska bir Arduino'yu (ornegin
        calisan bir projenin Nano'sunu) her denemede yeniden baslatirdi. Nano
        modunda yalnizca kullanicinin sectigi porta baglanilir.
        """
        return [] if cls.BOARD == "nano" else cls.candidate_ports()

    @classmethod
    def guess_device_port(cls):
        """Arayuzde varsayilan secilecek port (acmadan, tahmini)."""
        c = cls.candidate_ports()
        return c[0] if c else ""

    @staticmethod
    def _ports_signature():
        """Takili portlarin parmak izi; bir cihaz takilip cikarilinca degisir."""
        return tuple(sorted(
            (p.device, p.serial_number or "", p.location or "")
            for p in serial.tools.list_ports.comports() if p.vid is not None
        ))

    def connect_port(self, port):
        """port bos ise cihaz otomatik aranir."""
        self.user_selected_port = port or None
        self.cmd_queue.put(("CONNECT", port or ""))

    def disconnect_port(self):
        self.cmd_queue.put(("DISCONNECT", None))

    def disconnect_port_synchronous(self, timeout=3.0):
        """Portu kapatir ve kapanana kadar bekler (firmware yuklemeden once).

        Port eskiden GUI thread'inden dogrudan kapatiliyordu; worker thread
        o sirada ayni portta read() icindeyken bu pyserial'in ic durumunu
        (Windows'ta OVERLAPPED yapisini) bozabiliyordu. Kapatmayi portun
        sahibi olan worker thread yapar.
        """
        self.cmd_queue.put(("DISCONNECT", None))
        deadline = time.time() + timeout
        while self.ser is not None and time.time() < deadline:
            time.sleep(0.02)

    def send_cmd(self, action_name, payload_dict):
        self.cmd_queue.put(("SEND_CMD", (action_name, payload_dict)))

    def stop_worker(self, timeout_ms=2000):
        """Thread'i durdurur; portu thread'in kendisi kapatir.

        Port eskiden burada, GUI thread'inden kapatilip thread'e yalnizca
        100 ms taniniyordu: thread o sirada read() icindeyse pyserial
        kapanmis bir tanimlayiciyla karsilasiyor, cogu zaman da thread
        program kapanirken hala calisiyordu. Okuma zaman asimi 50 ms
        oldugundan thread birkac on ms icinde kendiliginden cikar.
        """
        self.running = False
        self.cmd_queue.put(("QUIT", None))
        if not self.wait(timeout_ms) and self.ser:
            try:
                self.ser.close()
            except Exception:
                pass

    def run(self):
        last_check = 0
        while self.running:
            connected = bool(self.ser and self.ser.is_open)
            try:
                # Bagliyken okuma zaten _read_available icinde bloklaniyor,
                # kuyrugu kisa bakip gecmek yeterli. Bagli degilken ise
                # saniyede 40 kez bosuna uyanmak yerine kuyrukta bekle.
                cmd_type, data = self.cmd_queue.get(timeout=0.005 if connected else 0.25)
                if cmd_type == "CONNECT":
                    self._do_connect(data)
                elif cmd_type == "DISCONNECT":
                    self.auto_reconnect = False
                    self._do_disconnect()
                elif cmd_type == "SEND_CMD":
                    action_name, payload_dict = data
                    self._do_send_cmd(action_name, payload_dict)
                elif cmd_type == "QUIT":
                    break
            except queue.Empty:
                pass

            now = time.time()
            if self.auto_reconnect and (not self.ser or not self.ser.is_open) and (now - last_check > 1.5):
                last_check = now
                self._auto_connect()

            if self.ser and self.ser.is_open:
                try:
                    self._read_available()
                except Exception as e:
                    self.status_signal.emit(t("Bağlantı uyarısı: ", "Connection warning: ") + str(e))
                    self._do_disconnect()

        self._do_disconnect()

    def _auto_connect(self):
        """Kullanicinin sectigi portu, yoksa adaylari sirayla dener."""
        sig = self._ports_signature()
        if sig != self._rejected_signature:
            self._rejected_ports.clear()
            self._rejected_signature = sig

        targets = []
        if self.user_selected_port:
            targets.append(self.user_selected_port)
        targets += [p for p in self.probe_ports() if p not in targets]
        for port in targets:
            if port in self._rejected_ports:
                continue
            ser = self._open_verified(port)
            if ser:
                self._on_opened(ser, port)
                return True
            self._rejected_ports.add(port)
        return False

    def _open_verified(self, port):
        """Portu acar ve PING ile gercekten Mini Keyboard oldugunu dogrular.

        Dogrulanirsa acik Serial nesnesini, degilse None dondurur. 32U4
        port acilinca resetlenmez, cevap aninda gelir. Nano ise port acilinca
        sifirlanir ve once bootloader calisir (~1-2 sn); bu yuzden Nano'da
        daha uzun beklenir, PING her 0,5 sn'de yinelenir.
        """
        try:
            ser = serial.Serial(port, self.baudrate, timeout=0.2)
        except Exception as e:
            self.status_signal.emit(f"{port} " + t("açılamadı: ", "could not be opened: ") + str(e))
            return None
        try:
            time.sleep(0.05)
            ser.reset_input_buffer()
            buf = b""
            deadline = time.time() + (3.5 if self.BOARD == "nano" else 1.5)
            next_ping = 0.0
            while time.time() < deadline:
                if time.time() >= next_ping:
                    ser.write(b'{"cmd":"PING"}\n')
                    ser.flush()
                    next_ping = time.time() + 0.5
                buf += ser.read(ser.in_waiting or 1)
                if self.DEVICE_NAME.encode() in buf:
                    return ser
                if len(buf) > 4096:
                    buf = buf[-512:]
        except Exception:
            pass
        try:
            ser.close()
        except Exception:
            pass
        return None

    def _on_opened(self, ser, port):
        ser.timeout = self.READ_TIMEOUT
        self.ser = ser
        self._rx_buffer = b""
        self.current_port = port
        self.user_selected_port = port
        self.auto_reconnect = True
        self.connected_signal.emit(True, t(f"{port} portuna bağlandı.", f"Connected to {port}."))

    def _read_available(self):
        """Blocks in the driver for up to the port's READ_TIMEOUT instead of
        the old 1 ms sleep/poll loop, which woke this thread ~1000x a second
        (each wake costing an in_waiting ioctl and a queue.Empty exception)
        even with the device idle. A byte on the wire ends the block
        immediately, so key latency gets better, not worse."""
        chunk = self.ser.read(1)
        if not chunk:
            return None
        pending = self.ser.in_waiting
        if pending:
            chunk += self.ser.read(pending)
        return self._consume_rx(chunk)

    def _consume_rx(self, chunk):
        """Parses only whole newline-terminated messages. The previous
        readline()-based path handed back a half-received line whenever the
        port timeout landed mid-message, so that key event was silently
        dropped; buffering the remainder means it survives to the next read.
        Returns the first non-event (command response) message, if any."""
        self._rx_buffer += chunk
        while True:
            idx = self._rx_buffer.find(b"\n")
            if idx < 0:
                return None
            raw = self._rx_buffer[:idx]
            self._rx_buffer = self._rx_buffer[idx + 1:]
            line = raw.decode('utf-8', errors='ignore').strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(msg, dict):
                continue  # gurultuden gelen "1" gibi gecerli ama nesne olmayan JSON baglantiyi koparmasin
            event = msg.get("event")
            if event == "KEY_PRESS":
                self.key_pressed_signal.emit(int(msg.get("id", 0)), str(msg.get("type", "down")))
            elif event == "MODE_CHANGE":
                self.mode_changed_signal.emit(int(msg.get("mode", 0)))
            else:
                return msg

    # Doubles as the idle wake interval of the reader thread (see
    # _read_available) and as the upper bound on how long a queued GUI command
    # waits before the thread notices it.
    READ_TIMEOUT = 0.05

    def _do_connect(self, port):
        """port bos ise otomatik arar; doluysa o portu dogrulayarak acar."""
        if self.ser and self.ser.is_open and (not port or port == self.current_port):
            return  # zaten bagli
        self._do_disconnect()
        self.auto_reconnect = True
        if not port:
            self._rejected_signature = None  # elle istendi: bastan tara
            if not self._auto_connect():
                self.connected_signal.emit(False, t("Mini Keyboard bulunamadı (kart takılı mı?)", "Mini Keyboard not found (is the board plugged in?)"))
            return
        ser = self._open_verified(port)
        if ser:
            self._on_opened(ser, port)
        else:
            # Yanlis port secildiyse orada israr etme; otomatik arama devralir.
            self.user_selected_port = None
            self._rejected_ports.add(port)
            self.connected_signal.emit(False, t(f"{port} bir Mini Keyboard değil (PING yanıtı yok).", f"{port} is not a Mini Keyboard (no PING reply)."))

    def _do_disconnect(self):
        # Bagli degilken "baglanti kesildi" yayinlanmaz: her baglanma
        # denemesi once buradan gectigi icin arayuz, bagli olmadigi halde
        # kopma bildirimi aliyor ve durum satirindaki asil mesaji eziyordu.
        was_connected = self.ser is not None
        if self.ser:
            try:
                if self.ser.is_open:
                    self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.current_port = None
        self._rx_buffer = b""
        if was_connected:
            self.connected_signal.emit(False, DISCONNECTED)

    def _do_send_cmd(self, action_name, payload_dict):
        if not self.ser or not self.ser.is_open:
            self.command_response_signal.emit(action_name, {}, t("Cihaz bağlı değil.", "Device not connected."))
            return

        try:
            payload_str = json.dumps(payload_dict, separators=(',', ':')) + "\n"
            self.ser.write(payload_str.encode('utf-8'))
            self.ser.flush()

            start_time = time.time()
            resp_dict = {}
            timeout_limit = 3.5 if action_name in ["SET_CONFIG", "SAVE", "SET_MODE"] else 1.5

            while self.running and time.time() - start_time < timeout_limit:
                data = self._read_available()
                if data is not None:
                    resp_dict = data
                    break

            if resp_dict:
                self.command_response_signal.emit(action_name, resp_dict, "OK")
            elif self.running:
                # Firmware her komuta (PING, GET_CONFIG, SET_*, SAVE) bir satir
                # cevap veriyor; cevap gelmemesi komutun islenmedigi demek.
                # Eskiden burada da "SUCCESS" yayinlaniyordu, yani kart ayari
                # hic almasa bile arayuz kartin elindeki ayari guncel sanip
                # sonraki mod degisimlerinde yalnizca SET_MODE gonderiyordu.
                self.command_response_signal.emit(
                    action_name, {"status": "FAILED"},
                    t(f"Kart {timeout_limit:.1f} sn içinde yanıt vermedi.", f"Board did not respond within {timeout_limit:.1f} s."))
        except Exception as e:
            # Onceden burada da {"status": "SUCCESS"} yayinlaniyordu - yani seri
            # yazma sirasinda gercek bir hata olsa (ör. cihaz komut ortasinda
            # cikarilirsa) bile UI'a "basarili" diye bildiriliyordu, hicbir status
            # mesaji da gorunmuyordu. Diger hata yollariyla (run() ici okuma
            # dongusu) tutarli olacak sekilde gercek hatayi bildiriyoruz.
            self.status_signal.emit(f"Komut gonderilemedi ({action_name}): {str(e)}")
            self.command_response_signal.emit(action_name, {"status": "FAILED"}, str(e))
