"""Uygulama açma: kurulu uygulama taraması, yönetici (Task Scheduler) görevleri,
.lnk çözme, çalışan pencereyi bulma/öne getirme. main.py'den davranış değişmeden ayrıldı."""
import logging
import os
import re
import ctypes
import subprocess
import threading

from i18n import t
from ctypes import wintypes

import psutil

from key_input import user32

_logger = logging.getLogger("MiniKeyboard")
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def fix_drive_relative_path(path):
    """Repairs a drive-relative path like "D:Games\\..." (missing the
    backslash after the drive letter) that an older saved config can
    contain - see the sys_drives fix in get_installed_windows_apps, which
    is what used to produce these via os.path.join("D:", "Games")."""
    if path and re.match(r"^[A-Za-z]:[^\\/]", path):
        return path[:2] + "\\" + path[2:]
    return path


_installed_apps_cache = None

def get_installed_windows_apps():
    """ Scans Windows Start Menu, Desktops, Games folders across all drives,
        Steam/EA/Riot/Epic libraries, and System paths to return all apps & games.
        Cached at module level: this walk takes ~0.3s and KeyConfigWidget
        creates two instances (single-click/long-press tabs) at startup, so
        without caching it used to run twice for an identical result. """
    global _installed_apps_cache
    if _installed_apps_cache is not None:
        return _installed_apps_cache

    apps = [
        (t("🧮 Hesap Makinesi", "🧮 Calculator"), "calc.exe"),
        (t("📝 Not Defteri", "📝 Notepad"), "notepad.exe"),
        (t("📁 Dosya Gezgini", "📁 File Explorer"), "explorer.exe"),
        ("⚡ Windows PowerShell", "powershell.exe"),
        (t("💻 Komut İstemi (CMD)", "💻 Command Prompt (CMD)"), "cmd.exe"),
        (t("📊 Görev Yöneticisi", "📊 Task Manager"), "taskmgr.exe"),
        (t("✂️ Ekran Alıntısı Aracı", "✂️ Snipping Tool"), "snippingtool.exe"),
        (t("🖥️ Uzak Masaüstü Bağlantısı", "🖥️ Remote Desktop Connection"), "mstsc.exe")
    ]
    
    seen_names = {a[0].lower() for a in apps}
    
    # 1. Desktop & Start Menu Shortcuts (.lnk and .url)
    shortcut_dirs = [
        os.path.expandvars(r"%PUBLIC%\Desktop"),
        os.path.expandvars(r"%USERPROFILE%\Desktop"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs")
    ]

    skip_keywords = [
        "uninstall", "kaldır", "readme", "help", "yardım", "website",
        "documentation", "vcredist", "cleanup", "touchup", "redist",
        "crashreportclient", "installer", "setup"
    ]

    for base_dir in shortcut_dirs:
        if os.path.exists(base_dir):
            for root, _, files in os.walk(base_dir):
                for f in files:
                    if f.endswith((".lnk", ".url")):
                        lower_f = f.lower()
                        if any(skip in lower_f for skip in skip_keywords):
                            continue
                        app_name = f.rsplit(".", 1)[0]
                        if app_name.lower() not in seen_names:
                            seen_names.add(app_name.lower())
                            full_path = os.path.join(root, f)
                            is_game = any(g in lower_f for g in [
                                "fc", "cs", "counter", "fifa", "valorant", "gta",
                                "game", "steam", "epic", "riot", "ea", "battle"
                            ])
                            prefix = "🎮 " if is_game else "🚀 "
                            apps.append((f"{prefix}{app_name}", full_path))

    # 2. Drive Roots Game Folders (D:\Games, C:\Games, E:\Games, F:\Games) & Launchers
    # Trailing backslash matters: os.path.join("D:", "Games") returns the
    # drive-relative "D:Games" (no separator inserted), not "D:\Games" -
    # that malformed path can still resolve at scan time (each drive has
    # its own implicit current directory) but breaks later lookups like
    # os.path.isfile() from a different working directory.
    sys_drives = ['C:\\', 'D:\\', 'E:\\', 'F:\\', 'G:\\']
    game_search_paths = []
    for d in sys_drives:
        for sub in [
            r"Games",
            r"Program Files (x86)\Steam\steamapps\common",
            r"SteamLibrary\steamapps\common",
            r"Program Files\EA Games",
            r"EA Games",
            r"Riot Games",
            r"Program Files\Epic Games",
            r"Epic Games"
        ]:
            p = os.path.join(d, sub)
            if os.path.exists(p):
                game_search_paths.append(p)

    for base_dir in game_search_paths:
        for root, _, files in os.walk(base_dir):
            depth = root[len(base_dir):].count(os.sep)
            if depth > 3:
                continue
            for f in files:
                if f.endswith(".exe"):
                    lower_f = f.lower()
                    if any(skip in lower_f for skip in skip_keywords):
                        continue
                    exe_name = f[:-4]
                    if any(g in lower_f or g in root.lower() for g in [
                        "fc", "cs", "counter", "fifa", "valorant", "gta", "game", "riot", "play"
                    ]):
                        folder_name = os.path.basename(root)
                        display_title = folder_name if (len(exe_name) < 4 or exe_name.lower() in ["launch", "play", "game"]) else exe_name
                        if display_title.lower() not in seen_names:
                            seen_names.add(display_title.lower())
                            full_path = os.path.join(root, f)
                            apps.append((f"🎮 {display_title}", full_path))

    apps.sort(key=lambda x: x[0].lower())
    _installed_apps_cache = apps
    return apps

_apps_by_norm_path_cache = None


def get_app_name_for_path(path):
    """Reverse lookup (normalized path -> display name) over the installed-app
    list, built once. The call sites run on every key select and every mode
    switch; scanning the list and calling os.path.normpath on all entries each
    time showed up as a visible stall when a key is assigned an app action."""
    global _apps_by_norm_path_cache
    if not path:
        return None
    if _apps_by_norm_path_cache is None:
        _apps_by_norm_path_cache = {}
        for name, p in get_installed_windows_apps():
            _apps_by_norm_path_cache.setdefault(os.path.normpath(p), name)
    return _apps_by_norm_path_cache.get(os.path.normpath(path))


def scheduled_task_name_for_app(path):
    """Deterministic Task Scheduler name for a given app path, e.g.
    'C:\\Program Files\\My App\\My App.exe' -> 'MiniKeyboard_MyApp'. Lets
    launch_app_path find a pre-set-up elevated task for an app without
    needing to store the mapping anywhere - it's derived the same way
    every time. Task names are matched case-insensitively by Windows."""
    base = os.path.splitext(os.path.basename(path))[0]
    sanitized = re.sub(r'[^A-Za-z0-9]', '', base)
    return f"MiniKeyboard_{sanitized}" if sanitized else None

def scheduled_task_exists(task_name):
    try:
        res = subprocess.run(
            ["schtasks", "/query", "/tn", task_name],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5
        )
        return res.returncode == 0
    except Exception:
        return False

def run_scheduled_task(task_name):
    try:
        res = subprocess.run(
            ["schtasks", "/run", "/tn", task_name],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=10
        )
        # Cikis kodu 0 degilse (gorev bozuk/silinmis) basarisiz say; cagiran
        # UAC'li acilisa dusebilsin.
        return res.returncode == 0
    except Exception:
        return False

def create_admin_launch_task(app_path):
    """Registers a 'MiniKeyboard_<AppName>' Scheduled Task (Run With
    Highest Privileges) for app_path, so launch_app_path can start it
    elevated without a UAC prompt from then on. Registering a task at this
    privilege level itself requires an elevated caller, so this triggers
    one UAC prompt via ShellExecute's "runas" verb on schtasks.exe directly
    (avoids nested-quoting issues from routing through a shell/PowerShell)."""
    task_name = scheduled_task_name_for_app(app_path)
    if not task_name or scheduled_task_exists(task_name):
        return True
    params = f'/create /tn "{task_name}" /tr "\\"{app_path}\\"" /sc ONCE /st 00:00 /rl HIGHEST /f'
    try:
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "schtasks.exe", params, None, 0)
        return int(result) > 32
    except Exception:
        return False

def delete_admin_launch_task(app_path):
    """Removes the Scheduled Task created by create_admin_launch_task, if
    any. Also requires elevation (same reason as creating it)."""
    task_name = scheduled_task_name_for_app(app_path)
    if not task_name or not scheduled_task_exists(task_name):
        return True
    params = f'/delete /tn "{task_name}" /f'
    try:
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "schtasks.exe", params, None, 0)
        return int(result) > 32
    except Exception:
        return False

_BROWSER_EXE_NAMES = {"brave.exe", "chrome.exe", "msedge.exe", "chrome_proxy.exe", "opera.exe"}

def resolve_shortcut(lnk_path):
    """Resolves a .lnk file's TargetPath/Arguments via WScript.Shell (same
    COM object save_and_update_desktop_shortcut already uses to write
    shortcuts). Needed to tell apart a real app shortcut from a
    browser-hosted PWA shortcut, which points at chrome_proxy.exe/brave.exe
    with a --app-id=<id> argument rather than at a dedicated executable.

    Sonuc (yol, degisiklik zamani) anahtariyla onbellekte: PowerShell cagrisi
    olcumde ~195 ms suruyordu ve .lnk hedefli her "uygulama ac" basisinda
    arayuz thread'inde tekrarlaniyordu. Kisayol dosyasi degismedikce hedefi de
    degismez. Acilista ayarlardaki kisayollar arka planda onceden cozulur
    (bkz. kisayollari_onceden_coz)."""
    try:
        anahtar = (os.path.normcase(lnk_path), os.path.getmtime(lnk_path))
    except OSError:
        anahtar = None
    if anahtar is not None:
        with _lnk_kilit:
            if anahtar in _lnk_onbellek:
                return _lnk_onbellek[anahtar]
    sonuc = _resolve_shortcut_powershell(lnk_path)
    if anahtar is not None and sonuc[0]:
        with _lnk_kilit:
            _lnk_onbellek[anahtar] = sonuc
    return sonuc


_lnk_onbellek = {}
_lnk_kilit = threading.Lock()


def kisayollari_onceden_coz(config):
    """Ayarlardaki tum .lnk yollarini arka planda cozer; ilk tus basisi da beklemesin."""
    yollar = set()

    def gez(o):
        if isinstance(o, dict):
            for v in o.values():
                gez(v)
        elif isinstance(o, list):
            for v in o:
                gez(v)
        elif isinstance(o, str) and o.lower().endswith(".lnk"):
            yollar.add(fix_drive_relative_path(o))

    gez(config)
    for yol in yollar:
        try:
            resolve_shortcut(os.path.normpath(yol))
        except Exception:
            pass


def _resolve_shortcut_powershell(lnk_path):
    ps_cmd = f"""
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut('{lnk_path}')
    Write-Output $lnk.TargetPath
    Write-Output $lnk.Arguments
    """
    try:
        CREATE_NO_WINDOW = 0x08000000
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, timeout=5
        )
        lines = res.stdout.splitlines()
        target = lines[0].strip() if len(lines) > 0 else ""
        args = lines[1].strip() if len(lines) > 1 else ""
        return target, args
    except Exception:
        return "", ""

def find_running_app_window(path):
    """Looks for a window belonging to an already-running instance of the
    app at `path`, so a repeated 'open program' key press can focus it
    instead of spawning a duplicate. Returns an HWND, or None.

    Direct .exe targets (games, normal apps) are matched by the owning
    process's exe name. .lnk shortcuts are resolved first: a PWA shortcut
    (browser exe + --app-id=<id> argument, e.g. the YouTube Music Desktop
    shortcut) is matched by that app id showing up in a browser process's
    command line, since the shortcut's own path never appears in any
    process - the browser is what's actually running. Any other .lnk is
    matched the same way as a direct .exe, using its resolved target."""
    norm_path = os.path.normpath(path)
    ext = os.path.splitext(norm_path)[1].lower()

    target_exe_name = None
    app_id = None

    if ext == ".lnk":
        target, args = resolve_shortcut(norm_path)
        if not target:
            return None
        target_name = os.path.basename(target).lower()
        if target_name in _BROWSER_EXE_NAMES:
            m = re.search(r'--app-id=([a-z0-9]+)', args or "", re.IGNORECASE)
            if m:
                app_id = m.group(1).lower()
            else:
                target_exe_name = target_name
        else:
            target_exe_name = target_name
    else:
        target_exe_name = os.path.basename(norm_path).lower()

    matching_pids = set()
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            name = (p.info['name'] or "").lower()
            if app_id:
                if name not in _BROWSER_EXE_NAMES:
                    continue
                cmdline = " ".join(p.info.get('cmdline') or []).lower()
                if f"--app-id={app_id}" in cmdline:
                    matching_pids.add(p.info['pid'])
            elif name == target_exe_name:
                matching_pids.add(p.info['pid'])
        except Exception:
            continue

    if not matching_pids:
        return None

    found_hwnd = [None]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_proc(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, 4):  # GW_OWNER - skip owned dialogs/tool windows
            return True
        if user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in matching_pids:
            found_hwnd[0] = hwnd
            return False
        return True

    user32.EnumWindows(_enum_proc, 0)
    return found_hwnd[0]

def activate_window(hwnd):
    """Brings an already-running app's window to the front, mimicking a
    taskbar-icon click: restore it if minimized, then force focus via the
    AttachThreadInput trick (Windows normally refuses SetForegroundWindow
    from a background process like this one)."""
    SW_RESTORE = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    fg_hwnd = user32.GetForegroundWindow()
    cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)

    attached_fg = fg_thread and fg_thread != cur_thread and user32.AttachThreadInput(cur_thread, fg_thread, True)
    attached_target = target_thread and target_thread != cur_thread and user32.AttachThreadInput(cur_thread, target_thread, True)
    try:
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    finally:
        if attached_fg:
            user32.AttachThreadInput(cur_thread, fg_thread, False)
        if attached_target:
            user32.AttachThreadInput(cur_thread, target_thread, False)
