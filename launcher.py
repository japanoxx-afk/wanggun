"""태조왕건 서버 런처 — 서버 관리 + 호스트 파일 조작 GUI.

배포: PyInstaller로 단일 exe 빌드.
  - 클라 유저: 런처.exe 하나만 있으면 동작 (호스트 파일 관리)
  - 서버 호스트: 런처.exe + dummyserver.py를 같은 폴더에 둔다
"""

import ctypes
import os
import re
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox

DOMAINS = [
    "wanggun.trigger.co.kr",
    "king.e2soft.com",
    "king.trigger.co.kr",
]
DEFAULT_IP = "26.157.67.215"
HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"


def get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
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
        r"C:\Python314\python.exe",
        r"C:\Python313\python.exe",
        r"C:\Python312\python.exe",
    ]:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


class ServerManager:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.proc = None
        self.server_script = os.path.join(base_dir, "dummyserver.py")
        self.bat_file = os.path.join(base_dir, "서버시작.bat")

    @property
    def has_server(self):
        return os.path.isfile(self.server_script)

    def start(self):
        if self.proc and self.proc.poll() is None:
            return False, "서버가 이미 실행 중입니다."

        if os.path.isfile(self.bat_file):
            self.proc = subprocess.Popen(
                ["cmd", "/c", self.bat_file],
                cwd=self.base_dir,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            return True, "서버를 시작했습니다."

        python = find_python()
        if not python:
            return False, (
                "Python이 설치되어 있지 않습니다.\n"
                "서버 실행에는 Python이 필요합니다."
            )
        if not self.has_server:
            return False, (
                "dummyserver.py를 찾을 수 없습니다.\n"
                f"런처와 같은 폴더에 넣어주세요.\n({self.base_dir})"
            )
        self.proc = subprocess.Popen(
            [python, self.server_script],
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
    def read_current_ip():
        try:
            with open(HOSTS_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or not line:
                        continue
                    if DOMAINS[0] in line:
                        return line.split()[0]
        except OSError:
            pass
        return DEFAULT_IP

    @staticmethod
    def apply_ip(ip):
        try:
            with open(HOSTS_PATH, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            content = ""

        for domain in DOMAINS:
            pattern = re.compile(
                rf"^[^\S\n]*\S+\s+{re.escape(domain)}\s*$",
                re.MULTILINE,
            )
            content = pattern.sub("", content)

        content = content.rstrip("\n") + "\n"
        for domain in DOMAINS:
            content += f"{ip} {domain}\n"

        with open(HOSTS_PATH, "w", encoding="utf-8") as f:
            f.write(content)

    @staticmethod
    def open_hosts_file():
        subprocess.Popen(["notepad.exe", HOSTS_PATH])


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("태조왕건 서버 런처")
        self.geometry("440x340")
        self.resizable(False, False)

        base = get_base_dir()
        self.server = ServerManager(base)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_server_tab(notebook)
        self._build_client_tab(notebook)

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

        ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=16)

        if self.server.has_server:
            ttk.Label(frame, text="dummyserver.py 감지됨", foreground="green").pack(
                anchor="w"
            )
        else:
            ttk.Label(
                frame,
                text="※ 서버 기능을 사용하려면 dummyserver.py를\n"
                "   이 런처와 같은 폴더에 넣어주세요.",
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
            anchor="w", pady=(0, 12)
        )

        current_ip = HostsManager.read_current_ip()

        ip_frame = ttk.LabelFrame(frame, text="서버 호스트 변경", padding=12)
        ip_frame.pack(fill="x", pady=(0, 12))

        ttk.Label(ip_frame, text="서버 IP 주소:").pack(anchor="w")
        self.ip_var = tk.StringVar(value=current_ip)
        ip_entry = ttk.Entry(ip_frame, textvariable=self.ip_var, width=28)
        ip_entry.pack(anchor="w", pady=(4, 8))

        ttk.Button(
            ip_frame, text="IP 적용", command=self._on_apply_ip, width=14
        ).pack(anchor="w")

        domain_frame = ttk.LabelFrame(frame, text="매핑 도메인 목록", padding=8)
        domain_frame.pack(fill="x", pady=(0, 12))
        for d in DOMAINS:
            ttk.Label(domain_frame, text=f"  • {d}").pack(anchor="w")

        ttk.Button(
            frame, text="호스트 파일 직접 편집 (메모장)", command=self._on_open_hosts
        ).pack(anchor="w")

    def _on_apply_ip(self):
        ip = self.ip_var.get().strip()
        if not ip:
            messagebox.showwarning("입력 오류", "IP 주소를 입력하세요.")
            return
        try:
            HostsManager.apply_ip(ip)
            messagebox.showinfo(
                "완료",
                f"호스트 파일이 업데이트되었습니다.\n\n"
                + "\n".join(f"{ip}  {d}" for d in DOMAINS),
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


if __name__ == "__main__":
    run_as_admin()
    app = App()
    app.mainloop()
