"""Лаунчер «Оркестр · AI» для Windows: сервер на этом компьютере + окно приложения.

    pythonw launcher/orchestra_launcher.py          (или двойной клик по Orchestra.vbs)

Что делает:
  • один экземпляр на пользователя (именованный мьютекс);
  • поднимает сервер на 127.0.0.1 на свободном порту — снаружи компьютера он недоступен;
  • сервер — дочерний процесс в Job Object: закрыл окно или лаунчер упал — сервер гаснет сам;
  • данные — в %LOCALAPPDATA%\\Orchestra\\AI (вне репозитория), мастер-ключ защищён DPAPI;
  • первый запуск: разовый токен настройки передаётся окну сам, в консоль ничего вводить не нужно;
  • если установлена Ollama и не запущена — запускает (локальные модели без облака).
"""
import ctypes
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"
DATA = Path(os.environ.get("ORCHESTRA_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Orchestra" / "AI")
CREATE_NO_WINDOW = 0x08000000
TITLE = "Оркестр · AI"


def message(text: str) -> None:
    if "--check" in sys.argv:
        print(text)
        return
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x40)


def single_instance() -> bool:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    handle = k32.CreateMutexW(None, False, "Local\\Orchestra.Launcher")
    single_instance.handle = handle                                    # держим до выхода
    return ctypes.get_last_error() != 183                              # ERROR_ALREADY_EXISTS


def kill_on_close_job():
    """Job Object: дочерние процессы завершаются вместе с лаунчером, даже при аварийном выходе."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    job = k32.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000                     # KILL_ON_JOB_CLOSE
    if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        return None
    return job


def bind(job, proc: subprocess.Popen) -> None:
    if job:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject(job, int(proc._handle))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_json(url: str, timeout: float = 1.5):
    import json
    with urllib.request.urlopen(url, timeout=timeout) as r:                # noqa: S310 — только 127.0.0.1
        return json.loads(r.read().decode("utf-8"))


def ensure_ollama() -> None:
    try:
        http_json("http://127.0.0.1:11434/api/tags")
        return
    except OSError:
        pass
    exe = shutil.which("ollama") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe")
    if Path(exe).exists():
        # своя консоль скрыта, но не DETACHED: иначе каждый runner модели открывает чёрное окно
        subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)


def server_python() -> str:
    venv = SERVER / ".venv" / "Scripts" / "python.exe"
    return str(venv if venv.exists() else sys.executable)


def main() -> None:
    if not single_instance():
        message("Оркестр · AI уже запущена.")
        return
    try:
        import webview
    except ImportError:
        message("Нет pywebview: server\\.venv\\Scripts\\pip install -e server[desktop]")
        return
    if not (ROOT / "web" / "dist" / "index.html").exists():
        message("Интерфейс не собран: cd web; npm install; npm run build")
        return
    DATA.mkdir(parents=True, exist_ok=True)
    if os.environ.get("ORCHESTRA_LOCAL_MODELS", "1") != "0":
        ensure_ollama()

    port, token = free_port(), secrets.token_urlsafe(24)
    env = {**os.environ, "ORCHESTRA_MODE": "desktop", "ORCHESTRA_HOST": "127.0.0.1", "ORCHESTRA_PORT": str(port),
           "ORCHESTRA_DATA_DIR": str(DATA), "ORCHESTRA_SETUP_TOKEN": token, "PYTHONUTF8": "1"}
    log = open(DATA / "server.log", "ab")
    proc = subprocess.Popen([server_python(), "-m", "orchestra", "serve"], cwd=str(SERVER), env=env,
                            stdout=log, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
    job = kill_on_close_job()
    bind(job, proc)

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            message(f"Сервер не запустился. Журнал: {DATA / 'server.log'}")
            return
        try:
            state = http_json(f"{base}/api/auth/state")
            break
        except OSError:
            time.sleep(0.3)
    else:
        message(f"Сервер не ответил за минуту. Журнал: {DATA / 'server.log'}")
        proc.kill()
        return

    url = f"{base}/#token={token}" if state.get("setup_required") else base     # фрагмент не уходит на сервер и в логи
    if "--check" in sys.argv:                          # проверка без окна: сервер поднялся и отвечает
        print(f"ok {base} setup_required={state.get('setup_required')} data={DATA}")
        proc.terminate()
        proc.wait(timeout=10)
        return
    webview.create_window(TITLE, url, width=1440, height=900, min_size=(1024, 680), background_color="#EFEFEC",
                          text_select=True)
    try:
        webview.start(private_mode=False, storage_path=str(DATA / "webview"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
