"""태조왕건 서버 런처 — 서버 관리 + 호스트 파일 조작 GUI.

단일 exe로 빌드하면 dummyserver.py가 내장된다.
  - 더블클릭: GUI 런처
  - 내부적으로 "서버 시작" 클릭 시 같은 exe를 --server 모드로 재실행
"""

import ctypes
from ctypes import wintypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

APP_VERSION = "0.90003"

DEFAULT_DOMAINS = [
    "wanggun.trigger.co.kr",
    "king.e2soft.com",
    "king.trigger.co.kr",
]
DEFAULT_IP = "26.157.67.215"
SERVER_LOOPBACK = "127.0.0.1"
HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
CONFIG_FILE = "launcher_config.json"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/japanoxx-afk/wanggun/main/"
GITHUB_RAW_URL = GITHUB_RAW_BASE + "dummyserver.py"
VERSION_CHECK_URL = GITHUB_RAW_BASE + "version.json"
DEFAULT_GAME_DIR = r"C:\Program Files\태조왕건"
DDRAW_INI = "ddraw.ini"
RESOLUTIONS = [
    "640x480", "800x600", "1024x768", "1280x720", "1280x960",
    "1600x900", "1920x1080", "2560x1440", "3440x1440", "3840x2160",
]
SHADERS = [
    ("선명하게 (Lanczos)", "Lanczos"),
    ("부드럽게 (Bicubic)", "Bicubic"),
    ("기본 (Bilinear)", "Bilinear"),
    ("픽셀아트 보간 (xBR-lv2)", "xBR-lv2"),
    ("도트 그대로 (Nearest)", "Nearest neighbor"),
    ("catmull-rom (기본값)", "Shaders\\interpolation\\catmull-rom-bilinear.glsl"),
]
SHADER_VALUES = {label: val for label, val in SHADERS}


def get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def read_server_version(path_or_bytes):
    """dummyserver.py에서 SERVER_VERSION 값을 읽는다. 없으면 None(구버전)."""
    try:
        if isinstance(path_or_bytes, bytes):
            text = path_or_bytes.decode("utf-8", errors="ignore")
        else:
            with open(path_or_bytes, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        match = re.search(r'^SERVER_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
        return match.group(1) if match else None
    except OSError:
        return None


def get_effective_server_script():
    """서버 시작 시 실제로 실행될 dummyserver.py 경로를 반환.

    run_server_mode()와 같은 우선순위: exe 옆 로컬 파일 → 내장 파일.
    """
    base = get_base_dir()
    local = os.path.join(base, "dummyserver.py")
    if os.path.isfile(local):
        return local, "로컬(업데이트본)"
    bundled = os.path.join(get_resource_dir(), "dummyserver.py")
    if os.path.isfile(bundled):
        return bundled, "exe 내장본"
    return None, "없음"


def get_resource_dir():
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run_as_admin():
    if is_admin():
        return
    if getattr(sys, "frozen", False):
        exe = sys.executable
        args = ""
    else:
        exe = sys.executable
        args = f'"{os.path.abspath(__file__)}"'
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 1)
    sys.exit()


def find_python():
    for candidate in [
        shutil.which("python"),
        shutil.which("python3"),
        r"C:\Users\seo\AppData\Local\Programs\Python\Python314\python.exe",
    ]:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def load_config(base_dir):
    path = os.path.join(base_dir, CONFIG_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(base_dir, cfg):
    path = os.path.join(base_dir, CONFIG_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════
#  자동 업데이트
# ═══════════════════════════════════════════════════════

def check_for_update():
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(VERSION_CHECK_URL)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        latest = data.get("launcher_version", APP_VERSION)
        url = data.get("launcher_url", "")
        if latest > APP_VERSION:
            return latest, url
    except Exception:
        pass
    return None, None


def do_self_update(download_url):
    import urllib.request
    if not getattr(sys, "frozen", False):
        return False, "개발 모드에서는 자동 업데이트를 사용할 수 없습니다."

    current_exe = sys.executable
    new_exe = current_exe + ".new"

    try:
        urllib.request.urlretrieve(download_url, new_exe)
    except Exception as e:
        return False, f"다운로드 실패:\n{e}"

    bat_path = current_exe + ".update.bat"
    bat_content = (
        '@echo off\r\n'
        'echo 업데이트 중...\r\n'
        'timeout /t 2 /nobreak >nul\r\n'
        f'del "{current_exe}"\r\n'
        f'move "{new_exe}" "{current_exe}"\r\n'
        f'start "" "{current_exe}"\r\n'
        f'del "%~f0"\r\n'
    )
    with open(bat_path, "w", encoding="mbcs") as f:
        f.write(bat_content)

    subprocess.Popen(
        ["cmd", "/c", bat_path],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return True, ""


# ═══════════════════════════════════════════════════════
#  서버 모드 (--server)
# ═══════════════════════════════════════════════════════

def run_server_mode():
    kernel32 = ctypes.windll.kernel32
    kernel32.AllocConsole()
    kernel32.SetConsoleTitleW("태조왕건 더미 서버")

    sys.stdout = open("CONOUT$", "w", encoding="utf-8")
    sys.stderr = open("CONOUT$", "w", encoding="utf-8")
    sys.stdin = open("CONIN$", "r", encoding="utf-8")

    base = get_base_dir()
    os.chdir(base)

    # 업데이트된 로컬 파일을 우선, 없으면 exe 내장 버전 사용
    script = os.path.join(base, "dummyserver.py")
    if not os.path.isfile(script):
        script = os.path.join(get_resource_dir(), "dummyserver.py")

    if not os.path.isfile(script):
        print("오류: dummyserver.py를 찾을 수 없습니다.")
        input("Enter를 눌러 종료...")
        return

    with open(script, "r", encoding="utf-8") as f:
        code = f.read()

    exec(compile(code, script, "exec"), {
        "__name__": "__main__",
        "__file__": os.path.join(base, "dummyserver.py"),
    })


# ═══════════════════════════════════════════════════════
#  GUI 모드
# ═══════════════════════════════════════════════════════

class ServerManager:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.proc = None

    def start(self):
        if self.proc and self.proc.poll() is None:
            return False, "서버가 이미 실행 중입니다."

        try:
            HostsManager.apply_ip(SERVER_LOOPBACK, DEFAULT_DOMAINS)
        except OSError:
            pass

        if getattr(sys, "frozen", False):
            self.proc = subprocess.Popen(
                [sys.executable, "--server"],
                cwd=self.base_dir,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            return True, "서버를 시작했습니다. (hosts → 127.0.0.1)"

        bat = os.path.join(self.base_dir, "서버시작.bat")
        if os.path.isfile(bat):
            self.proc = subprocess.Popen(
                ["cmd", "/c", bat],
                cwd=self.base_dir,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            return True, "서버를 시작했습니다."

        python = find_python()
        script = os.path.join(self.base_dir, "dummyserver.py")
        if not python:
            return False, "Python이 설치되어 있지 않습니다."
        if not os.path.isfile(script):
            return False, "dummyserver.py를 찾을 수 없습니다."
        self.proc = subprocess.Popen(
            [python, script],
            cwd=self.base_dir,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        return True, "서버를 시작했습니다."

    def stop(self):
        if not self.proc or self.proc.poll() is not None:
            return False, "실행 중인 서버가 없습니다."
        self.proc.terminate()
        self.proc = None
        return True, "서버를 종료했습니다."

    def restart(self):
        self.stop()
        return self.start()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None


class HostsManager:
    @staticmethod
    def read_current_ip(domains):
        try:
            with open(HOSTS_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or not line:
                        continue
                    for d in domains:
                        if d in line:
                            return line.split()[0]
        except OSError:
            pass
        return DEFAULT_IP

    @staticmethod
    def apply_ip(ip, domains):
        try:
            with open(HOSTS_PATH, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            content = ""

        for domain in domains:
            pattern = re.compile(
                rf"^[^\S\n]*\S+\s+{re.escape(domain)}\s*$",
                re.MULTILINE,
            )
            content = pattern.sub("", content)

        content = content.rstrip("\n") + "\n"
        for domain in domains:
            content += f"{ip} {domain}\n"

        with open(HOSTS_PATH, "w", encoding="utf-8") as f:
            f.write(content)

    @staticmethod
    def open_hosts_file():
        subprocess.Popen(["notepad.exe", HOSTS_PATH])


class WindowModeManager:
    def __init__(self, game_dir):
        self.game_dir = game_dir

    @property
    def ini_path(self):
        return os.path.join(self.game_dir, DDRAW_INI)

    @property
    def available(self):
        return os.path.isfile(self.ini_path)

    def read_settings(self):
        result = {"windowed": False, "width": 800, "height": 600,
                  "shader": "", "maintas": False}
        if not self.available:
            return result
        try:
            with open(self.ini_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(";") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip()
                    if key == "windowed":
                        result["windowed"] = val.lower() == "true"
                    elif key == "width" and val.isdigit():
                        result["width"] = int(val)
                    elif key == "height" and val.isdigit():
                        result["height"] = int(val)
                    elif key == "shader":
                        result["shader"] = val
                    elif key == "maintas":
                        result["maintas"] = val.lower() == "true"
        except OSError:
            pass
        return result

    def apply_settings(self, windowed, width, height, shader="", maintas=False):
        if not self.available:
            return False, f"ddraw.ini를 찾을 수 없습니다.\n({self.ini_path})"
        try:
            with open(self.ini_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            return False, str(e)

        def set_value(text, key, value):
            pattern = re.compile(rf"^(\s*){re.escape(key)}\s*=.*$", re.MULTILINE)
            if pattern.search(text):
                return pattern.sub(rf"\g<1>{key}={value}", text)
            return text

        content = set_value(content, "windowed", "true" if windowed else "false")
        content = set_value(content, "fullscreen", "false" if windowed else "true")
        content = set_value(content, "width", str(width))
        content = set_value(content, "height", str(height))
        content = set_value(content, "maintas", "true" if maintas else "false")
        if shader:
            content = set_value(content, "shader", shader)

        try:
            with open(self.ini_path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            return False, str(e)

        mode = "창모드" if windowed else "전체화면"
        return True, f"{mode} ({width}x{height}) 적용 완료."


# ═══════════════════════════════════════════════════════
#  게임 패치 (비침습적: 게임 파일을 수정하지 않는다)
# ═══════════════════════════════════════════════════════

GAME_EXE_NAME = "wanggun.exe"
# 바이너리 패치(2·3번)를 대비한 백업 대상 파일들.
PATCH_TARGET_FILES = ["WangGun.exe", "KAURI.dll", "iCARUS.dll"]
BACKUP_DIR_NAME = "_원본백업"


def _proc_name(pid):
    """pid의 실행 파일 이름(소문자)."""
    if not pid:
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
    except OSError:
        pass
    finally:
        k32.CloseHandle(h)
    return ""


def _find_game_hwnd(exe_name=GAME_EXE_NAME):
    """실행 중인 게임의 최상위 창 HWND를 찾는다. 없으면 0."""
    user32 = ctypes.windll.user32
    result = {"hwnd": 0}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if _proc_name(pid.value) == exe_name:
            result["hwnd"] = hwnd
            return False
        return True

    user32.EnumWindows(_cb, 0)
    return result["hwnd"]


class ImeFixHelper:
    """게임 창이 포커스일 때 영문 입력을 강제해 한글 IME로 단축키가 막히는 문제를
    해결한다. 게임 파일을 수정하지 않는 백그라운드 감시 스레드."""

    def __init__(self, exe_name=GAME_EXE_NAME):
        self.exe_name = exe_name.lower()
        self._thread = None
        self._stop = threading.Event()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        user32 = ctypes.windll.user32
        imm32 = ctypes.windll.imm32
        user32.LoadKeyboardLayoutW.restype = ctypes.c_void_p
        user32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        WM_INPUTLANGCHANGEREQUEST = 0x0050
        KLF_ACTIVATE = 0x00000001
        en_hkl = user32.LoadKeyboardLayoutW("00000409", KLF_ACTIVATE)
        while not self._stop.is_set():
            try:
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if _proc_name(pid.value) == self.exe_name:
                        # 게임 창을 영문 레이아웃으로 전환 + IME 컨텍스트 해제.
                        user32.PostMessageW(
                            hwnd, WM_INPUTLANGCHANGEREQUEST, 0, en_hkl or 0
                        )
                        imm32.ImmAssociateContextEx(hwnd, 0, 0)
            except OSError:
                pass
            self._stop.wait(0.4)


class TimerOverlay:
    """게임 창 상단 중앙에 경과 시간을 표시하는 투명·클릭통과 오버레이.

    'arm'하면 게임 창이 나타날 때 자동으로 타이머가 뜨고(0부터), 게임 창이
    사라지면 숨는다. 즉 게임을 실행하면 자동으로 타이머가 시작된다.
    """

    def __init__(self, master, exe_name=GAME_EXE_NAME):
        self.master = master
        self.exe_name = exe_name.lower()
        self.win = None
        self.label = None
        self._start = 0.0
        self._after = None
        self.armed = False
        self._watch_after = None
        self._game_present = False

    @property
    def running(self):
        return self.win is not None

    def arm(self):
        """자동 시작 활성화: 게임 창을 감시해 나타나면 타이머를 띄운다."""
        self.armed = True
        self._game_present = False
        self._watch()

    def disarm(self):
        self.armed = False
        if self._watch_after is not None:
            try:
                self.master.after_cancel(self._watch_after)
            except tk.TclError:
                pass
            self._watch_after = None
        self.stop()

    def _watch(self):
        if not self.armed:
            return
        try:
            present = bool(_find_game_hwnd(self.exe_name))
            if present and not self._game_present:
                # 게임 창 등장 → 타이머 새로 시작(0부터)
                self.stop()
                self.start()
            elif not present and self._game_present:
                self.stop()
            self._game_present = present
        except Exception:
            pass
        self._watch_after = self.master.after(1000, self._watch)

    def start(self):
        if self.win:
            return
        self._start = time.time()
        self.win = tk.Toplevel(self.master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        # 반투명 창(-alpha)으로 렌더링. -transparentcolor는 수동 layered 스타일과
        # 충돌해 검은 박스만 나오고 글자가 안 보이는 문제가 있어 사용하지 않는다.
        try:
            self.win.attributes("-alpha", 0.82)
        except tk.TclError:
            pass
        self.win.configure(bg="#0d0d0d")
        self.label = tk.Label(
            self.win, text="00:00", fg="#FFD54A", bg="#0d0d0d",
            font=("Consolas", 22, "bold"), padx=12, pady=2,
        )
        self.label.pack()
        self.win.update_idletasks()
        self._make_clickthrough()
        self._tick()

    def reset(self):
        self._start = time.time()

    def stop(self):
        if self._after is not None:
            try:
                self.master.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None

    def _make_clickthrough(self):
        # -alpha가 이미 WS_EX_LAYERED를 켰으므로, 클릭 통과용 WS_EX_TRANSPARENT와
        # 포커스 방지 WS_EX_NOACTIVATE만 기존 스타일에 추가한다.
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE = 0x08000000
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, ex | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE,
            )
        except OSError:
            pass

    def _tick(self):
        if self.win is None:
            return
        try:
            elapsed = int(time.time() - self._start)
            self.label.config(text="%02d:%02d" % (elapsed // 60, elapsed % 60))
            self.win.update_idletasks()
            game = _find_game_hwnd(self.exe_name)
            if game:
                user32 = ctypes.windll.user32
                rect = wintypes.RECT()
                if user32.GetWindowRect(game, ctypes.byref(rect)):
                    w = max(self.win.winfo_reqwidth(), 60)
                    cx = rect.left + (rect.right - rect.left - w) // 2
                    self.win.geometry(f"+{cx}+{rect.top + 6}")
                    self.win.deiconify()
                    self.win.attributes("-topmost", True)
            # 게임 창을 못 찾아도 숨기지 않는다(탐지 실패 시 사라지는 문제 방지).
        except tk.TclError:
            return
        self._after = self.master.after(250, self._tick)


class GameFileBackup:
    """게임 원본 파일 백업/복원 (2·3번 바이너리 패치용 롤백 프레임)."""

    def __init__(self, game_dir):
        self.game_dir = game_dir

    def backup_dir(self):
        return os.path.join(self.game_dir, BACKUP_DIR_NAME)

    def has_backup(self):
        bd = self.backup_dir()
        return any(
            os.path.isfile(os.path.join(bd, f)) for f in PATCH_TARGET_FILES
        )

    def backup(self):
        bd = self.backup_dir()
        os.makedirs(bd, exist_ok=True)
        done = []
        for f in PATCH_TARGET_FILES:
            src = os.path.join(self.game_dir, f)
            dst = os.path.join(bd, f)
            if os.path.isfile(src) and not os.path.isfile(dst):
                shutil.copy2(src, dst)
                done.append(f)
        return done

    def restore(self):
        bd = self.backup_dir()
        done = []
        for f in PATCH_TARGET_FILES:
            src = os.path.join(bd, f)
            dst = os.path.join(self.game_dir, f)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
                done.append(f)
        return done


class BinaryPatch:
    """게임 실행 파일의 여러 바이트 구간을 안전하게 패치/복원한다.

    각 편집(edit)은 패치 구간을 앞(prefix)·뒤(suffix) 고정 문맥으로 특정한다.
    패치 후 구간이 바뀌어도 prefix/suffix는 그대로라 위치를 다시 찾을 수 있고,
    조합이 유일하며 구간이 orig/patched 중 하나일 때만 동작해 안전하다.
    edit = dict(prefix=bytes, orig=bytes, patched=bytes, suffix=bytes)
    """

    def __init__(self, game_dir, filename, edits):
        self.game_dir = game_dir
        self.filename = filename
        self.edits = edits

    def _path(self):
        return os.path.join(self.game_dir, self.filename)

    def _read(self):
        with open(self._path(), "rb") as f:
            return f.read()

    def _find_edit(self, data, edit):
        """편집 구간의 시작 위치. 유일하지 않으면 -2, 없으면 -1."""
        prefix, suffix = edit["prefix"], edit["suffix"]
        olen = len(edit["orig"])
        plen = len(prefix)
        matches = []
        start = 0
        while True:
            i = data.find(prefix, start)
            if i < 0:
                break
            rpos = i + plen
            region = data[rpos:rpos + olen]
            if (region in (edit["orig"], edit["patched"])
                    and data[rpos + olen: rpos + olen + len(suffix)] == suffix):
                matches.append(rpos)
            start = i + 1
        if not matches:
            return -1
        if len(matches) > 1:
            return -2
        return matches[0]

    def status(self):
        """'applied' | 'original' | 'partial' | 'notfound' | 'ambiguous' | 'nofile'"""
        if not os.path.isfile(self._path()):
            return "nofile"
        data = self._read()
        states = []
        for edit in self.edits:
            pos = self._find_edit(data, edit)
            if pos == -1:
                return "notfound"
            if pos == -2:
                return "ambiguous"
            region = data[pos:pos + len(edit["orig"])]
            states.append("applied" if region == edit["patched"] else "original")
        if all(s == "applied" for s in states):
            return "applied"
        if all(s == "original" for s in states):
            return "original"
        return "partial"

    def _set(self, use_patched):
        data = bytearray(self._read())
        # 먼저 모든 편집 위치를 확인(하나라도 실패하면 파일을 건드리지 않음)
        plan = []
        for edit in self.edits:
            pos = self._find_edit(bytes(data), edit)
            if pos < 0:
                raise RuntimeError("패치 위치를 찾을 수 없습니다 (버전 불일치?)")
            plan.append((pos, edit))
        for pos, edit in plan:
            region = edit["patched"] if use_patched else edit["orig"]
            data[pos:pos + len(region)] = region
        with open(self._path(), "wb") as f:
            f.write(bytes(data))

    def apply(self):
        self._set(True)

    def revert(self):
        self._set(False)


# ② 건설 중 랠리포인트 (WangGun.exe 패치, 역분석 기반)
# 랠리 설정 핸들러(0x40a640, waypoint[0] 좌표를 0x7eb58a에 저장)가 0x40a68d에서
# 건설 중(state & 0x1000; 건설시작 0x40a2b1이 state=0x100d 설정)이면 명령을
# 거부(jne 0x40ae0f)한다. 이 jne(6바이트)를 NOP으로 없애 건설 중에도 랠리를
# 받게 한다. 아울러 명령 그룹 필터(0x409880)의 건설 제외 분기도 jne→jmp로
# 풀어 건설 건물이 그룹에 남도록 한다.
RALLY_PATCH = dict(
    filename="WangGun.exe",
    edits=[
        # 랠리 설정 핸들러의 건설 게이트: jne 0x40ae0f → NOP×6
        dict(
            prefix=bytes.fromhex("250010000066 85c0".replace(" ", "")),  # and eax,0x1000; test ax,ax
            orig=bytes.fromhex("0f857c070000"),                          # jne 0x40ae0f
            patched=bytes.fromhex("909090909090"),                       # nop×6
            suffix=bytes.fromhex("8b5c2428"),                            # mov ebx,[esp+0x28]
        ),
        # 명령 그룹 필터의 건설 제외 분기: jne(0x75) → jmp(0xEB)
        dict(
            prefix=bytes.fromhex("6681f90010"),                          # cmp cx,0x1000
            orig=bytes.fromhex("75"),                                    # jne
            patched=bytes.fromhex("eb"),                                 # jmp
            suffix=bytes.fromhex("0966ff8d74768500eb13"),                # 09; dec word[..]; jmp
        ),
    ],
)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"태조왕건 서버 런처 v{APP_VERSION}")
        self.geometry("520x580")
        self.resizable(True, True)
        self.minsize(460, 520)

        self.base_dir = get_base_dir()
        self.server = ServerManager(self.base_dir)
        self.cfg = load_config(self.base_dir)
        self.domains = list(self.cfg.get("domains", DEFAULT_DOMAINS))

        game_dir = self.cfg.get("game_dir", DEFAULT_GAME_DIR)
        self.winmode = WindowModeManager(game_dir)

        # 게임 패치 도우미 (비침습적)
        self.ime_helper = ImeFixHelper()
        self.timer_overlay = TimerOverlay(self)
        self.backup = GameFileBackup(game_dir)
        self.rally_patch = BinaryPatch(game_dir, **RALLY_PATCH)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        self._build_server_tab(notebook)
        self._build_client_tab(notebook)
        self._build_settings_tab(notebook)
        self._build_patch_tab(notebook)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        launch_frame = ttk.Frame(self)
        launch_frame.pack(fill="x", padx=8, pady=(0, 10))
        ttk.Button(
            launch_frame, text="게임 실행 (WangGun.exe)",
            command=self._on_launch_game,
        ).pack(anchor="center")

        self._update_status()

    def _build_server_tab(self, notebook):
        frame = ttk.Frame(notebook, padding=16)
        notebook.add(frame, text="  호스트 (서버)  ")

        ttk.Label(frame, text="더미 서버 관리", font=("맑은 고딕", 12, "bold")).pack(
            anchor="w", pady=(0, 12)
        )

        self.status_var = tk.StringVar(value="서버 상태: 꺼짐")
        ttk.Label(frame, textvariable=self.status_var, font=("맑은 고딕", 10)).pack(
            anchor="w", pady=(0, 12)
        )

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x")

        self.btn_start = ttk.Button(
            btn_frame, text="서버 시작", command=self._on_start, width=14
        )
        self.btn_start.pack(side="left", padx=(0, 8))

        self.btn_restart = ttk.Button(
            btn_frame, text="서버 재시작", command=self._on_restart, width=14
        )
        self.btn_restart.pack(side="left", padx=(0, 8))

        self.btn_stop = ttk.Button(
            btn_frame, text="서버 종료", command=self._on_stop, width=14
        )
        self.btn_stop.pack(side="left")

        update_frame = ttk.Frame(frame)
        update_frame.pack(fill="x", pady=(12, 0))

        ttk.Button(
            update_frame, text="서버 업데이트 (GitHub)", command=self._on_update, width=24
        ).pack(side="left")

        self.update_status_var = tk.StringVar()
        ttk.Label(update_frame, textvariable=self.update_status_var, foreground="gray").pack(
            side="left", padx=(8, 0)
        )

        ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=16)

        # 서버 시작 시 실제로 실행될 스크립트와 그 버전을 표시한다.
        # 구버전(버전 표기 없음)이 실행될 상황이면 빨간색으로 경고.
        self.server_ver_var = tk.StringVar()
        self.server_ver_label = ttk.Label(frame, textvariable=self.server_ver_var)
        self.server_ver_label.pack(anchor="w")
        self._refresh_server_version()

    def _refresh_server_version(self):
        script, source = get_effective_server_script()
        if script is None:
            self.server_ver_var.set("※ dummyserver.py를 찾을 수 없습니다.")
            self.server_ver_label.configure(foreground="red")
            return

        version = read_server_version(script)
        if version:
            self.server_ver_var.set(f"서버 스크립트: v{version} ({source})")
            self.server_ver_label.configure(foreground="green")
        else:
            self.server_ver_var.set(
                f"서버 스크립트: 구버전 ({source}) — [서버 업데이트] 버튼을 눌러주세요!"
            )
            self.server_ver_label.configure(foreground="red")

    def _on_start(self):
        ok, msg = self.server.start()
        self._update_status()
        if not ok:
            messagebox.showwarning("서버", msg)

    def _on_stop(self):
        ok, msg = self.server.stop()
        self._update_status()
        if not ok:
            messagebox.showinfo("서버", msg)

    def _on_restart(self):
        ok, msg = self.server.restart()
        self._update_status()
        if not ok:
            messagebox.showwarning("서버", msg)

    def _on_update(self):
        import urllib.request
        import urllib.error

        self.update_status_var.set("다운로드 중...")
        self.update()

        dest = os.path.join(self.base_dir, "dummyserver.py")
        try:
            req = urllib.request.Request(GITHUB_RAW_URL)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()

            with open(dest, "wb") as f:
                f.write(data)

            size_kb = len(data) / 1024
            new_ver = read_server_version(data) or "?"
            self.update_status_var.set(f"완료 v{new_ver} ({size_kb:.0f}KB)")
            self._refresh_server_version()
            messagebox.showinfo(
                "업데이트",
                f"dummyserver.py를 v{new_ver}(으)로 업데이트했습니다.\n"
                f"({size_kb:.0f}KB 다운로드)\n\n"
                f"서버가 실행 중이면 재시작해야 적용됩니다.",
            )
        except urllib.error.URLError as e:
            self.update_status_var.set("실패")
            messagebox.showerror("업데이트 실패", f"다운로드 오류:\n{e}")
        except OSError as e:
            self.update_status_var.set("실패")
            messagebox.showerror("업데이트 실패", f"파일 저장 오류:\n{e}")

    def _update_status(self):
        if self.server.running:
            self.status_var.set("서버 상태: 실행 중 ●")
            self.btn_start.state(["disabled"])
        else:
            self.status_var.set("서버 상태: 꺼짐 ○")
            self.btn_start.state(["!disabled"])
        self.after(2000, self._update_status)

    def _build_client_tab(self, notebook):
        frame = ttk.Frame(notebook, padding=16)
        notebook.add(frame, text="  클라 (접속)  ")

        ttk.Label(frame, text="호스트 파일 관리", font=("맑은 고딕", 12, "bold")).pack(
            anchor="w", pady=(0, 8)
        )

        ip_frame = ttk.LabelFrame(frame, text="서버 호스트 변경", padding=10)
        ip_frame.pack(fill="x", pady=(0, 8))

        ip_row = ttk.Frame(ip_frame)
        ip_row.pack(fill="x")

        ttk.Label(ip_row, text="서버 IP:").pack(side="left")
        current_ip = HostsManager.read_current_ip(self.domains)
        self.ip_var = tk.StringVar(value=current_ip)
        ttk.Entry(ip_row, textvariable=self.ip_var, width=22).pack(
            side="left", padx=(6, 8)
        )
        ttk.Button(ip_row, text="IP 적용", command=self._on_apply_ip, width=10).pack(
            side="left"
        )

        domain_frame = ttk.LabelFrame(frame, text="매핑 도메인 목록 (편집 가능)", padding=8)
        domain_frame.pack(fill="both", expand=True, pady=(0, 8))

        list_container = ttk.Frame(domain_frame)
        list_container.pack(fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_container, orient="vertical")
        scrollbar.pack(side="right", fill="y")

        self.domain_listbox = tk.Listbox(
            list_container,
            height=6,
            font=("Consolas", 10),
            yscrollcommand=scrollbar.set,
        )
        self.domain_listbox.pack(fill="both", expand=True)
        scrollbar.config(command=self.domain_listbox.yview)

        for d in self.domains:
            self.domain_listbox.insert(tk.END, d)

        domain_btn_frame = ttk.Frame(domain_frame)
        domain_btn_frame.pack(fill="x", pady=(6, 0))

        ttk.Button(
            domain_btn_frame, text="추가", command=self._on_add_domain, width=8
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            domain_btn_frame, text="삭제", command=self._on_remove_domain, width=8
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            domain_btn_frame, text="기본값 복원", command=self._on_reset_domains, width=12
        ).pack(side="left")

        bottom_frame = ttk.Frame(frame)
        bottom_frame.pack(fill="x")

        ttk.Button(
            bottom_frame, text="호스트 파일 열기", command=self._on_open_hosts
        ).pack(side="left")

    def _sync_domains(self):
        self.domains = list(self.domain_listbox.get(0, tk.END))
        self.cfg["domains"] = self.domains
        save_config(self.base_dir, self.cfg)

    def _on_add_domain(self):
        dlg = tk.Toplevel(self)
        dlg.title("도메인 추가")
        dlg.geometry("340x100")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        ttk.Label(dlg, text="도메인 주소:").pack(anchor="w", padx=12, pady=(12, 4))
        var = tk.StringVar()
        entry = ttk.Entry(dlg, textvariable=var, width=40)
        entry.pack(padx=12)
        entry.focus_set()

        def confirm(event=None):
            val = var.get().strip()
            if val:
                self.domain_listbox.insert(tk.END, val)
                self._sync_domains()
            dlg.destroy()

        entry.bind("<Return>", confirm)
        ttk.Button(dlg, text="추가", command=confirm).pack(pady=8)

    def _on_remove_domain(self):
        sel = self.domain_listbox.curselection()
        if not sel:
            messagebox.showinfo("알림", "삭제할 도메인을 선택하세요.")
            return
        self.domain_listbox.delete(sel[0])
        self._sync_domains()

    def _on_reset_domains(self):
        self.domain_listbox.delete(0, tk.END)
        for d in DEFAULT_DOMAINS:
            self.domain_listbox.insert(tk.END, d)
        self._sync_domains()

    def _on_apply_ip(self):
        ip = self.ip_var.get().strip()
        if not ip:
            messagebox.showwarning("입력 오류", "IP 주소를 입력하세요.")
            return
        self._sync_domains()
        if not self.domains:
            messagebox.showwarning("입력 오류", "도메인 목록이 비어 있습니다.")
            return
        try:
            HostsManager.apply_ip(ip, self.domains)
            messagebox.showinfo(
                "완료",
                "호스트 파일이 업데이트되었습니다.\n\n"
                + "\n".join(f"{ip}  {d}" for d in self.domains),
            )
        except PermissionError:
            messagebox.showerror(
                "권한 오류",
                "호스트 파일 수정에 관리자 권한이 필요합니다.\n"
                "런처를 관리자 권한으로 실행해 주세요.",
            )
        except OSError as e:
            messagebox.showerror("오류", str(e))

    def _on_open_hosts(self):
        HostsManager.open_hosts_file()

    # ── 게임 패치 탭 ─────────────────────────────────────
    def _build_patch_tab(self, notebook):
        frame = ttk.Frame(notebook, padding=16)
        notebook.add(frame, text="  게임 패치  ")

        ttk.Label(
            frame,
            text="게임 파일을 수정하지 않는 실시간 도우미입니다.\n"
                 "끄면 즉시 원래대로 돌아갑니다 (롤백).",
            foreground="gray", justify="left",
        ).pack(anchor="w", pady=(0, 10))

        # 1) 한영 단축키 수정
        ime_frame = ttk.LabelFrame(
            frame, text="① 한영 단축키 수정", padding=10
        )
        ime_frame.pack(fill="x", pady=(0, 10))
        self.ime_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            ime_frame,
            text="게임 중 영문 입력 강제 (임시방편)",
            variable=self.ime_var, command=self._on_toggle_ime,
        ).pack(anchor="w")
        ttk.Label(
            ime_frame,
            text="※ 켜면 단축키는 항상 되지만 한글 채팅/치트가 막힙니다.\n"
                 "   '한글 상태에서도 단축키가 되게' 하는 근본 패치는 게임의 키\n"
                 "   입력 처리를 손봐야 해 조사 중입니다. 기본은 꺼짐.",
            foreground="gray", justify="left", font=("", 8),
        ).pack(anchor="w", pady=(4, 0))

        # 4) 게임 타이머
        timer_frame = ttk.LabelFrame(
            frame, text="④ 게임 타이머 (상단 중앙)", padding=10
        )
        timer_frame.pack(fill="x", pady=(0, 10))
        self.timer_var = tk.BooleanVar(value=False)
        row = ttk.Frame(timer_frame)
        row.pack(fill="x")
        ttk.Checkbutton(
            row, text="타이머 사용 (게임 실행 시 자동 시작)",
            variable=self.timer_var, command=self._on_toggle_timer,
        ).pack(side="left")
        ttk.Button(row, text="타이머 리셋", command=self._on_reset_timer).pack(
            side="right"
        )
        ttk.Label(
            timer_frame,
            text="※ 체크해 두면 게임 창이 뜰 때 자동으로 0부터 시작합니다.\n"
                 "   전투 시작 시점에 맞추려면 '타이머 리셋'을 누르세요.\n"
                 "   창모드에서 잘 보입니다 (설정 탭에서 창모드 권장).",
            foreground="gray", justify="left", font=("", 8),
        ).pack(anchor="w", pady=(4, 0))

        # ② 건설 중 랠리포인트 (바이너리 패치)
        rally_frame = ttk.LabelFrame(
            frame, text="② 건설 중 랠리포인트 (실험적 · WangGun.exe 패치)", padding=10
        )
        rally_frame.pack(fill="x", pady=(0, 10))
        self.rally_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            rally_frame,
            text="건설 중인 건물에도 랠리포인트 지정 허용",
            variable=self.rally_var, command=self._on_toggle_rally,
        ).pack(anchor="w")
        self.rally_status = tk.StringVar()
        ttk.Label(
            rally_frame, textvariable=self.rally_status, foreground="gray",
            font=("", 8),
        ).pack(anchor="w", pady=(2, 0))
        ttk.Label(
            rally_frame,
            text="※ 아직 조사 중인 기능입니다. 명령 실행부 2곳을 패치했으나\n"
                 "   실제로는 적용되지 않는 것으로 확인돼(게이트가 입력 처리\n"
                 "   쪽으로 추정) 추가 분석이 필요합니다. 적용 전 자동 백업하며\n"
                 "   체크 해제 시 원복됩니다.",
            foreground="gray", justify="left", font=("", 8),
        ).pack(anchor="w", pady=(2, 0))

        # ③ 유닛 스폰 위치 — 조사 결과
        spawn_frame = ttk.LabelFrame(
            frame, text="③ 유닛 스폰을 랠리 최근접으로 — 조사 결과", padding=10
        )
        spawn_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(
            spawn_frame,
            text="스폰 위치 선택은 조건 하나가 아니라 알고리즘 재작성(코드 주입)이\n"
                 "필요해 바이너리 패치로는 위험이 큽니다. 현재는 비권장입니다.",
            foreground="gray", justify="left", font=("", 8),
        ).pack(anchor="w")

        # 원본 백업/롤백 (전체)
        bk_frame = ttk.LabelFrame(frame, text="원본 백업 / 롤백", padding=10)
        bk_frame.pack(fill="x")
        bk_row = ttk.Frame(bk_frame)
        bk_row.pack(fill="x")
        ttk.Button(
            bk_row, text="원본 백업", command=self._on_backup, width=14
        ).pack(side="left")
        ttk.Button(
            bk_row, text="롤백 (원본 복원)", command=self._on_rollback, width=18
        ).pack(side="left", padx=(8, 0))
        self.backup_status = tk.StringVar()
        ttk.Label(
            bk_frame, textvariable=self.backup_status, foreground="gray"
        ).pack(anchor="w", pady=(4, 0))
        self._refresh_backup_status()
        self._refresh_rally_status()

    def _on_toggle_ime(self):
        if self.ime_var.get():
            self.ime_helper.start()
        else:
            self.ime_helper.stop()

    def _on_toggle_timer(self):
        if self.timer_var.get():
            # 게임 실행 시 자동으로 타이머가 뜨도록 감시 시작.
            self.timer_overlay.arm()
            # 이미 게임이 실행 중이면 즉시 표시.
            if _find_game_hwnd(self.timer_overlay.exe_name):
                self.timer_overlay.stop()
                self.timer_overlay.start()
        else:
            self.timer_overlay.disarm()

    def _on_reset_timer(self):
        if self.timer_overlay.running:
            self.timer_overlay.reset()

    def _refresh_backup_status(self):
        self.backup.game_dir = self.cfg.get("game_dir", DEFAULT_GAME_DIR)
        if self.backup.has_backup():
            self.backup_status.set("원본 백업 있음 ✓")
        else:
            self.backup_status.set("원본 백업 없음")

    def _refresh_rally_status(self):
        self.rally_patch.game_dir = self.cfg.get("game_dir", DEFAULT_GAME_DIR)
        st = self.rally_patch.status()
        msg = {
            "applied": "상태: 적용됨 ✓",
            "original": "상태: 미적용 (원본)",
            "partial": "상태: 일부만 적용됨 — 체크하면 완전 적용됩니다",
            "notfound": "상태: 패치 지점 없음 (버전 불일치?)",
            "ambiguous": "상태: 시그니처 중복 — 안전상 미적용",
            "nofile": "상태: WangGun.exe 없음",
        }.get(st, st)
        self.rally_status.set(msg)
        self.rally_var.set(st == "applied")

    def _on_toggle_rally(self):
        self.rally_patch.game_dir = self.cfg.get("game_dir", DEFAULT_GAME_DIR)
        want = self.rally_var.get()
        st = self.rally_patch.status()
        if st in ("notfound", "ambiguous", "nofile"):
            messagebox.showerror("패치 불가", self.rally_status.get())
            self._refresh_rally_status()
            return
        try:
            if want:
                # 적용 전 원본 자동 백업
                self.backup.game_dir = self.rally_patch.game_dir
                self.backup.backup()
                self.rally_patch.apply()
            else:
                self.rally_patch.revert()
        except (OSError, RuntimeError) as e:
            messagebox.showerror(
                "패치 실패",
                f"{e}\n\n게임이 실행 중이면 종료 후 다시 시도하세요.",
            )
        self._refresh_backup_status()
        self._refresh_rally_status()

    def _on_backup(self):
        self.backup.game_dir = self.cfg.get("game_dir", DEFAULT_GAME_DIR)
        try:
            done = self.backup.backup()
        except OSError as e:
            messagebox.showerror("백업 실패", str(e))
            return
        self._refresh_backup_status()
        if done:
            messagebox.showinfo("백업", f"백업 완료: {', '.join(done)}")
        else:
            messagebox.showinfo("백업", "이미 백업이 있거나 대상 파일이 없습니다.")

    def _on_rollback(self):
        self.backup.game_dir = self.cfg.get("game_dir", DEFAULT_GAME_DIR)
        if not self.backup.has_backup():
            messagebox.showinfo("롤백", "백업이 없습니다.")
            return
        if not messagebox.askyesno("롤백", "원본 파일로 복원하시겠습니까?"):
            return
        try:
            done = self.backup.restore()
        except OSError as e:
            messagebox.showerror("롤백 실패", str(e))
            return
        messagebox.showinfo("롤백", f"원본 복원 완료: {', '.join(done)}")

    def _on_close(self):
        try:
            self.ime_helper.stop()
            self.timer_overlay.disarm()
        except Exception:
            pass
        self.destroy()

    def _on_launch_game(self):
        game_dir = self.cfg.get("game_dir", DEFAULT_GAME_DIR)
        exe = os.path.join(game_dir, "WangGun.exe")
        if not os.path.isfile(exe):
            messagebox.showerror(
                "오류",
                f"WangGun.exe를 찾을 수 없습니다.\n({exe})\n\n"
                "설정 탭에서 게임 경로를 확인하세요.",
            )
            return
        try:
            subprocess.Popen([exe], cwd=game_dir)
        except OSError as e:
            messagebox.showerror("실행 오류", str(e))

    # ── 설정 탭 ──────────────────────────────────────────
    def _build_settings_tab(self, notebook):
        frame = ttk.Frame(notebook, padding=16)
        notebook.add(frame, text="  설정  ")

        ttk.Label(frame, text="게임 창모드 설정", font=("맑은 고딕", 12, "bold")).pack(
            anchor="w", pady=(0, 12)
        )

        # ── 게임 경로 ──
        path_frame = ttk.LabelFrame(frame, text="게임 설치 경로", padding=10)
        path_frame.pack(fill="x", pady=(0, 10))

        self.gamedir_var = tk.StringVar(value=self.winmode.game_dir)
        ttk.Entry(path_frame, textvariable=self.gamedir_var, width=48).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(
            path_frame, text="찾기", command=self._on_browse_game, width=6
        ).pack(side="left")

        # ── 창모드 ──
        mode_frame = ttk.LabelFrame(frame, text="디스플레이 모드", padding=12)
        mode_frame.pack(fill="x", pady=(0, 10))

        settings = self.winmode.read_settings()

        self.windowed_var = tk.BooleanVar(value=settings["windowed"])
        ttk.Checkbutton(
            mode_frame, text="창모드로 실행 (Alt+Enter로 토글 가능)",
            variable=self.windowed_var,
        ).pack(anchor="w", pady=(0, 6))

        self.maintas_var = tk.BooleanVar(value=settings.get("maintas", False))
        ttk.Checkbutton(
            mode_frame, text="비율 유지 (4:3 비율 고정)",
            variable=self.maintas_var,
        ).pack(anchor="w", pady=(0, 10))

        res_row = ttk.Frame(mode_frame)
        res_row.pack(fill="x", pady=(0, 4))

        ttk.Label(res_row, text="해상도:").pack(side="left")
        current_res = f"{settings['width']}x{settings['height']}"
        self.res_var = tk.StringVar(value=current_res)
        res_combo = ttk.Combobox(
            res_row, textvariable=self.res_var, values=RESOLUTIONS, width=14
        )
        res_combo.pack(side="left", padx=(6, 0))

        # ── 업스케일 셰이더 ──
        shader_frame = ttk.LabelFrame(frame, text="업스케일 셰이더 (고해상도 화질 개선)", padding=12)
        shader_frame.pack(fill="x", pady=(0, 10))

        current_shader = settings.get("shader", "")
        shader_label = current_shader
        for label, val in SHADERS:
            if val == current_shader:
                shader_label = label
                break

        self.shader_var = tk.StringVar(value=shader_label)
        shader_labels = [label for label, _ in SHADERS]
        shader_combo = ttk.Combobox(
            shader_frame, textvariable=self.shader_var,
            values=shader_labels, width=30, state="readonly",
        )
        shader_combo.pack(anchor="w", pady=(0, 6))

        ttk.Label(
            shader_frame,
            text="Lanczos = 가장 선명  |  xBR-lv2 = 도트를 곡선으로 보간\n"
                 "게임 내부 800x600 → 설정 해상도로 업스케일합니다.",
            foreground="gray",
        ).pack(anchor="w")

        # ── 적용 ──
        ttk.Button(
            frame, text="설정 적용", command=self._on_apply_winmode, width=14
        ).pack(anchor="w", pady=(4, 0))

        if not self.winmode.available:
            ttk.Label(
                frame,
                text="※ ddraw.ini를 찾을 수 없습니다. 게임 경로를 확인하세요.",
                foreground="red",
            ).pack(anchor="w", pady=(8, 0))

    def _on_browse_game(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(
            title="태조왕건 설치 폴더 선택",
            initialdir=self.gamedir_var.get(),
        )
        if d:
            self.gamedir_var.set(d)
            self.winmode.game_dir = d
            self.cfg["game_dir"] = d
            save_config(self.base_dir, self.cfg)

    def _on_apply_winmode(self):
        game_dir = self.gamedir_var.get().strip()
        if game_dir != self.winmode.game_dir:
            self.winmode.game_dir = game_dir
            self.cfg["game_dir"] = game_dir
            save_config(self.base_dir, self.cfg)

        res = self.res_var.get().strip()
        try:
            w, h = res.split("x")
            width, height = int(w), int(h)
        except (ValueError, AttributeError):
            messagebox.showwarning("입력 오류", "해상도 형식: 800x600")
            return

        shader_label = self.shader_var.get()
        shader_val = SHADER_VALUES.get(shader_label, shader_label)
        ok, msg = self.winmode.apply_settings(
            self.windowed_var.get(), width, height,
            shader=shader_val, maintas=self.maintas_var.get(),
        )
        if ok:
            messagebox.showinfo("설정", msg)
        else:
            messagebox.showerror("오류", msg)


# ═══════════════════════════════════════════════════════
#  엔트리포인트
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    if "--server" in sys.argv:
        run_server_mode()
    else:
        run_as_admin()

        latest, url = check_for_update()
        if latest and url:
            import tkinter as _tk
            _root = _tk.Tk()
            _root.withdraw()
            do_update = messagebox.askyesno(
                "업데이트 알림",
                f"새 버전이 있습니다!\n\n"
                f"현재: v{APP_VERSION}  →  최신: v{latest}\n\n"
                f"지금 업데이트하시겠습니까?",
            )
            _root.destroy()
            if do_update:
                ok, err = do_self_update(url)
                if ok:
                    sys.exit()
                else:
                    _root2 = _tk.Tk()
                    _root2.withdraw()
                    messagebox.showerror("업데이트 실패", err)
                    _root2.destroy()
        elif latest and not url:
            import tkinter as _tk
            _root = _tk.Tk()
            _root.withdraw()
            messagebox.showinfo(
                "업데이트 알림",
                f"새 버전 v{latest}이 있습니다.\n"
                f"GitHub에서 다운로드해 주세요.\n\n"
                f"https://github.com/japanoxx-afk/wanggun/releases",
            )
            _root.destroy()

        app = App()
        app.mainloop()
