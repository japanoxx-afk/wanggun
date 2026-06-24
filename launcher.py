"""태조왕건 서버 런처 — 서버 관리 + 호스트 파일 조작 GUI."""

import ctypes
import os
import re
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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BAT_PATH = os.path.join(BASE_DIR, "서버시작.bat")


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run_as_admin():
    if not is_admin():
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas",
            sys.executable, f'"{os.path.abspath(__file__)}"',
            None, 1,
        )
        sys.exit()


class ServerManager:
    def __init__(self):
        self.proc = None

    def start(self):
        if self.proc and self.proc.poll() is None:
            return False, "서버가 이미 실행 중입니다."
        self.proc = subprocess.Popen(
            ["cmd", "/c", BAT_PATH],
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
        self.geometry("420x320")
        self.resizable(False, False)

        self.server = ServerManager()

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_server_tab(notebook)
        self._build_client_tab(notebook)

        self._update_status()

    # ── 서버 탭 ──────────────────────────────────────────
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

        ttk.Label(frame, text=f"배치 파일: {BAT_PATH}", wraplength=380).pack(
            anchor="w"
        )

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

    # ── 클라이언트 탭 ────────────────────────────────────
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
