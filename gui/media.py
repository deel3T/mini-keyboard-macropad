"""Medya: SMTC (hedefli oynatıcı) sunucusu, oynatıcı listesi, müzik/video
tarayıcısı rolleri ve medya tuşu yönlendirmesi."""
import logging
import os
import queue
import threading
import time
import types
import subprocess

from PyQt6.QtCore import QObject, pyqtSignal

from key_input import send_system_media_key

_logger = logging.getLogger("MiniKeyboard")
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

MEDIA_ITEMS_TR = [
    ("Sesi Kapat / Aç", 1),
    ("Sesi Artır", 2),
    ("Sesi Azalt", 3),
    ("Oynat / Durdur", 4),
    ("Sonraki Parça", 5),
    ("Önceki Parça", 6),
    ("İleri Sar (10 sn)", 10),
    ("Geri Sar (10 sn)", 11),
]
MEDIA_ITEMS_EN = [
    ("Mute / Unmute", 1),
    ("Volume Up", 2),
    ("Volume Down", 3),
    ("Play / Pause", 4),
    ("Next Track", 5),
    ("Previous Track", 6),
    ("Seek Forward (10 s)", 10),
    ("Seek Back (10 s)", 11),
]
MEDIA_GLYPHS = {1: "🔇", 2: "🔊", 3: "🔉", 4: "⏯", 5: "⏭", 6: "⏮", 10: "⏩", 11: "⏪"}
# Hedef oynatici yalnizca bunlar icin anlamli; ses (1-3) her zaman sistem
# geneli gider, oturum basina ses kontrolu SMTC'de yok.
PLAYER_TARGETABLE_CODES = {4, 5, 6, 10, 11}
SMTC_COMMANDS = {4: "PlayPause", 5: "Next", 6: "Previous"}
SMTC_COMMANDS = {4: "PlayPause", 5: "Next", 6: "Previous", 10: "SeekForward", 11: "SeekBack"}
# Eski profil surumlerinin kodlari: 7/8/9 -> 4/5/6.
LEGACY_CODE_ALIASES = {7: 4, 8: 5, 9: 6}

# Rol hedefleri: tus belirli bir oynaticiya degil "muzik tarayicisi" ya da
# "video tarayicisi" rolune baglanabilir. Hangi tarayicinin bu rolu ustlendigi
# Ayarlar sayfasindan secilir; secim degisince o role bagli butun tuslar
# kendiliginden yeni tarayiciya gider.
MUSIC_ROLE = "role:music"
VIDEO_ROLE = "role:video"
ROLE_KEYS = (MUSIC_ROLE, VIDEO_ROLE)
_role_targets = {MUSIC_ROLE: "", VIDEO_ROLE: ""}

# Ayarlar sayfasindaki hazir tarayici listesi. Deger, SMTC kimliginin
# (SourceAppUserModelId) bir PARCASI; smtc.ps1 tam kimlik bulamazsa parca
# olarak eslestirir. Listede olmayan bir uygulama (Spotify, bir PWA...) o an
# acik oturumlardan da secilebilir.
BROWSER_PRESETS = (
    ("Chrome", "Google Chrome"),
    ("MSEdge", "Microsoft Edge"),
    ("Brave", "Brave"),
    ("Opera", "Opera"),
    ("Vivaldi", "Vivaldi"),
    ("Helium", "Helium"),
    ("308046B0AF4A39CB", "Mozilla Firefox"),
)


def set_role_targets(music="", video=""):
    _role_targets[MUSIC_ROLE] = music or ""
    _role_targets[VIDEO_ROLE] = video or ""


def role_target(role):
    return _role_targets.get(role, "")


def resolve_player(player):
    """Rol ise Ayarlar'daki tarayiciya cevirir; degilse oldugu gibi doner."""
    if player in _role_targets:
        return _role_targets[player]
    return player


def media_action_target(act):
    """Bir medya eyleminin (kod, hedef_oynatici) cifti."""
    code = act.get("code", 4)
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = 4
    return LEGACY_CODE_ALIASES.get(code, code), act.get("player", "") or ""


SMTC_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smtc.ps1")


def _run_smtc_tek(args, timeout):
    # WinRT medya API'leri yalnizca Windows PowerShell 5.1'de var (pwsh'de yok).
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SMTC_SCRIPT] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW,
    )


class _SmtcSunucu:
    """smtc.ps1'in kalici (-Serve) kopyasi.

    Eskiden her hedefli medya tusu yeni bir PowerShell sureci baslatiyordu:
    olcum (08.10) basis basina ~265 ms (PowerShell + WinRT yuklemesi). Kalici
    surecte ilk istek ~250 ms, sonrakiler 1-6 ms. Surec olurse ya da yanit
    vermezse oldurulur, o istek tek seferlik yola duser, sonraki istek
    yeniden baslatir. Yanit sonu: "\\x1eSON <cikis kodu>" satiri.
    """
    SON = "\x1eSON "

    def __init__(self):
        self._p = None
        self._kuyruk = None
        self._kilit = threading.Lock()

    def _baslat(self):
        self._p = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SMTC_SCRIPT, "-Serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW)
        # readline zaman asimi icin satirlar ayri bir thread'de kuyruga alinir.
        kuyruk = self._kuyruk = queue.Queue()
        cikis = self._p.stdout

        def oku():
            for satir in cikis:
                kuyruk.put(satir)
            kuyruk.put(None)  # surec bitti
        threading.Thread(target=oku, name="MiniKB-SMTC-Oku", daemon=True).start()

    def _durdur(self):
        p, self._p = self._p, None
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass

    def isit(self):
        """Ilk medya tusu da beklemesin diye sureci onceden baslatir (arka planda cagrilir)."""
        try:
            self.calistir(["-List"], timeout=10)
        except Exception:
            pass

    def calistir(self, args, timeout):
        """_run_smtc_tek ile ayni arayuz: .returncode ve .stdout tasiyan nesne."""
        if args[:1] == ["-List"]:
            istek = "List"
        elif len(args) == 4 and args[0] == "-Command" and args[2] == "-Target":
            istek = f"Cmd\t{args[1]}\t{args[3]}"
        else:
            return _run_smtc_tek(args, timeout)
        with self._kilit:
            try:
                if self._p is None or self._p.poll() is not None:
                    self._baslat()
                self._p.stdin.write(istek + "\n")
                self._p.stdin.flush()
                satirlar, bitis = [], time.monotonic() + timeout
                while True:
                    satir = self._kuyruk.get(timeout=max(0.01, bitis - time.monotonic()))
                    if satir is None:
                        raise OSError("SMTC sureci kapandi")
                    if satir.startswith(self.SON):
                        return types.SimpleNamespace(returncode=int(satir[len(self.SON):].strip() or 1),
                                                     stdout="".join(satirlar))
                    satirlar.append(satir)
            except (OSError, ValueError, queue.Empty) as exc:
                _logger.warning("Kalici SMTC sureci yanit vermedi (%s); tek seferlik yola dusuluyor.", exc)
                self._durdur()
        return _run_smtc_tek(args, timeout)

    def kapat(self):
        with self._kilit:
            self._durdur()


_smtc_sunucu = _SmtcSunucu()


def _run_smtc(args, timeout):
    return _smtc_sunucu.calistir(args, timeout)


def smtc_available():
    return os.path.isfile(SMTC_SCRIPT)


def _friendly_player_name(aumid):
    """SMTC kimliginden okunakli ad: "Brave._crx_..." -> "Brave (uygulama)"."""
    if not aumid:
        return ""
    if aumid.lower().endswith(".exe"):
        return os.path.splitext(os.path.basename(aumid))[0].capitalize()
    base = aumid.split("!", 1)[0]
    head = base.split("_", 1)[0].split(".", 1)[0]
    if "._crx_" in base:
        return f"{head} (uygulama)"
    return head or aumid


class _PlayerListCache(QObject):
    """Acik SMTC oynaticilarinin listesi.

    Liste bir PowerShell sureciyle (~0.3 sn) alinir. Linux surumu bunu
    arayuz thread'inde yapip 2 sn onbellekliyor; Windows'ta surec baslatmak
    daha pahali oldugu icin liste arka planda yenilenir ve hazir olunca
    `updated` sinyali ile acik tus ayar panelleri kendini yeniden doldurur.
    Yenile dugmesi (force) beklemeli calisir.
    """
    updated = pyqtSignal()
    TTL = 5.0

    def __init__(self):
        super().__init__()
        self._items = []
        self._time = 0.0
        self._busy = False
        self._lock = threading.Lock()

    def _query(self):
        try:
            res = _run_smtc(["-List"], timeout=8)
        except Exception as exc:
            _logger.warning("SMTC oynatici listesi alinamadi: %s", exc)
            return None
        items, seen = [], set()
        for line in (res.stdout or "").splitlines():
            parts = line.split("\t")
            aumid = parts[0].strip()
            if not aumid or aumid in seen:
                continue
            seen.add(aumid)
            title = parts[2].strip() if len(parts) > 2 else ""
            display = _friendly_player_name(aumid)
            if title:
                display = f"{display} — {title[:40]}"
            items.append((aumid, display))
        return items

    def _store(self, items):
        with self._lock:
            # Basarisiz sorguda liste korunur ama zaman yine guncellenir: aksi
            # halde "updated" -> refresh_players -> get() her seferinde yeni
            # bir sorgu baslatir ve PowerShell calismadigi surece ~0.3 sn'de
            # bir kendini tekrarlayan bir dongu olusurdu.
            if items is not None:
                self._items = items
            self._time = time.time()

    def _background(self):
        try:
            self._store(self._query())
        finally:
            self._busy = False
            self.updated.emit()

    def get(self, force=False):
        if force:
            self._store(self._query())
        elif time.time() - self._time > self.TTL and not self._busy:
            self._busy = True
            threading.Thread(target=self._background, name="MiniKB-SMTC", daemon=True).start()
        with self._lock:
            return list(self._items)


_player_cache = None


def player_cache():
    global _player_cache
    if _player_cache is None:
        _player_cache = _PlayerListCache()
    return _player_cache


def list_players(force=False):
    return player_cache().get(force=force)


def player_display_name(player):
    """Su an kapali olan kayitli bir oynaticinin gorunen adi."""
    for key, name in BROWSER_PRESETS:
        if player == key:
            return name
    return _friendly_player_name(player)


_last_trigger_by_code = {}
# Ayni komutun tekrar engeli. Parca atlama / oynat-durdur / sessiz icin
# 0.40 sn (cift olay parcayi iki kez atlatiyordu). Ses arttir/azalt icin
# bu engel art arda hizli basislari yutuyordu; orada yalnizca tus
# sekmesine karsi kisa bir sure birakildi.
# 10/11 (sarma): art arda basislar dusurulmesin.
_REPEAT_GUARD = {2: 0.05, 3: 0.05, 10: 0.05, 11: 0.05}
_DEFAULT_REPEAT_GUARD = 0.40


def trigger_media(code, player=""):
    """Tek giris noktasi (eylem thread'inde calisir).

    Hedef oynatici verilmisse komut YALNIZCA o SMTC oturumuna gider. Oturum
    bulunamazsa sistem geneli tusa DUSULMEZ: aksi halde komut o an calan
    baska bir oynaticiya giderdi. Hedefli tus ya hedefini etkiler ya hicbir
    seyi. Rol hedefi (muzik/video tarayicisi) Ayarlar'da secilmemisse tus
    sistem geneli calisir.
    """
    code = LEGACY_CODE_ALIASES.get(code, code)
    if not code:
        return
    now = time.monotonic()
    if now - _last_trigger_by_code.get(code, 0.0) < _REPEAT_GUARD.get(code, _DEFAULT_REPEAT_GUARD):
        return
    _last_trigger_by_code[code] = now

    if player in _role_targets:
        resolved = _role_targets[player]
        if not resolved:
            _logger.info("%s icin Ayarlar'da tarayici secilmemis; sistem geneli calisiyor.", player)
        player = resolved

    if code in (10, 11) and not player:
        # Sarma icin sistem geneli bir medya tusu yok: Windows'un o an
        # odaktaki oturumuna (smtc.ps1'de bos hedef) gonderilir.
        player = "*"
    command = SMTC_COMMANDS.get(code)
    if command and player:
        if not smtc_available():
            _logger.info("smtc.ps1 yok; hedefli medya tusu (%s) gonderilmedi.", player)
            return
        # PowerShell ~0.3 sn surer; eylem kuyrugunu bekletmemek icin (arkasindan
        # basilan kisayol gecikmesin) ayri bir thread'de beklenir. Linux
        # surumu de playerctl'i beklemeden (Popen) calistiriyor.
        threading.Thread(target=_smtc_hedefli, args=(code, command, player),
                         name="MiniKB-SMTC-Komut", daemon=True).start()
        return
    send_system_media_key(code)


def _smtc_hedefli(code, command, player):
    try:
        res = _run_smtc(["-Command", command, "-Target", player], timeout=6)
        if res.returncode != 0:
            _logger.info("Hedef oynatici (%s) bulunamadi/yanit vermedi (kod %s); komut gonderilmedi.",
                         player, res.returncode)
    except Exception as exc:
        _logger.warning("SMTC komutu calistirilamadi: %s", exc)
