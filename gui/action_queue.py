"""Tuş eylemleri için sıralı arka plan iş kuyruğu. main.py'den davranış değişmeden ayrıldı."""
import logging
import queue
import threading

_logger = logging.getLogger("MiniKeyboard")


# Tus eylemleri (kisayol, metin makrosu, medya) arayuz thread'inin disinda,
# tek bir sirali is kuyrugunda calisir. Metin makrosu tuslari time.sleep ile
# basili tutuyor, medya komutu bir PowerShell sureci bekliyor; bunlar arayuz
# thread'inde calisirken pencere donuyor, gelen tus olaylari da bekliyordu.
# Tek thread: art arda basilan iki makronun tuslari birbirine karismamali.
_action_queue = queue.Queue()
_action_thread = None
_action_thread_lock = threading.Lock()


def _action_worker():
    while True:
        job = _action_queue.get()
        if job is None:
            return
        fn, args, kwargs = job
        try:
            fn(*args, **kwargs)
        except Exception:
            _logger.exception("Tus eylemi calistirilamadi: %s", getattr(fn, "__name__", fn))


def run_in_background(fn, *args, **kwargs):
    global _action_thread
    with _action_thread_lock:
        if _action_thread is None or not _action_thread.is_alive():
            _action_thread = threading.Thread(target=_action_worker, name="MiniKB-Actions", daemon=True)
            _action_thread.start()
    _action_queue.put((fn, args, kwargs))


def shutdown_actions(timeout=1.0):
    global _action_thread
    with _action_thread_lock:
        thread, _action_thread = _action_thread, None
    if thread is not None and thread.is_alive():
        _action_queue.put(None)
        thread.join(timeout)
