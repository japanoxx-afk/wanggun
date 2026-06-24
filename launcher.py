"""태조왕건 서버 런처 — 서버 관리 + 호스트 파일 조작 GUI.

단일 exe로 빌드하면 dummyserver.py가 내장된다.
  - 더블클릭: GUI 런처
  - 내부적으로 "서버 시작" 클릭 시 같은 exe를 --server 모드로 재실행
"""

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox

APP_VERSION = "0.5"

DEFAULT_DOMAINS = [
    "wanggun.trigger.co.kr",
    "king.e2soft.com",
    "king.trigger.co.kr",
]
DEFAULT_IP = "26.157.67.215"
SERVER_LOOPBACK = "127.0.0.1"
HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
CONFIG_FILE = "launcher_config.json"
GITHUB_RAW_URL = "https://raw.githubusercontent.com/japanoxx-afk/wanggun/main/dummyserver.py"
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

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_server_tab(notebook)
        self._build_client_tab(notebook)
        self._build_settings_tab(notebook)

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

        if getattr(sys, "frozen", False):
            ttk.Label(frame, text="서버 내장 모드 (단일 exe)", foreground="green").pack(
                anchor="w"
            )
        else:
            script = os.path.join(self.base_dir, "dummyserver.py")
            if os.path.isfile(script):
                ttk.Label(frame, text="dummyserver.py 감지됨", foreground="green").pack(
                    anchor="w"
                )
            else:
                ttk.Label(
                    frame,
                    text="※ dummyserver.py를 같은 폴더에 넣어주세요.",
                    foreground="gray",
                ).pack(anchor="w")

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
            self.update_status_var.set(f"완료 ({size_kb:.0f}KB)")
            messagebox.showinfo(
                "업데이트",
                f"dummyserver.py를 최신 버전으로 업데이트했습니다.\n"
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
        app = App()
        app.mainloop()
