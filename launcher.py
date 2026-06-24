"""태조왕건 서버 런처 — 서버 관리 + 호스트 파일 조작 GUI.

배포: PyInstaller로 단일 exe 빌드.
  - 클라 유저: 런처.exe 하나만 있으면 동작 (호스트 파일 관리)
  - 서버 호스트: 런처.exe + dummyserver.py를 같은 폴더에 둔다
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

DEFAULT_DOMAINS = [
    "wanggun.trigger.co.kr",
    "king.e2soft.com",
    "king.trigger.co.kr",
]
DEFAULT_IP = "26.157.67.215"
HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
CONFIG_FILE = "launcher_config.json"


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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("태조왕건 서버 런처")
        self.geometry("520x520")
        self.resizable(True, True)
        self.minsize(460, 460)

        self.base_dir = get_base_dir()
        self.server = ServerManager(self.base_dir)
        self.cfg = load_config(self.base_dir)
        self.domains = list(self.cfg.get("domains", DEFAULT_DOMAINS))

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

    # ── 클라이언트 탭 ────────────────────────────────────
    def _build_client_tab(self, notebook):
        frame = ttk.Frame(notebook, padding=16)
        notebook.add(frame, text="  클라 (접속)  ")

        ttk.Label(frame, text="호스트 파일 관리", font=("맑은 고딕", 12, "bold")).pack(
            anchor="w", pady=(0, 8)
        )

        # ── IP 입력 ──
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

        # ── 도메인 목록 ──
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

        # ── 하단 버튼 ──
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


if __name__ == "__main__":
    run_as_admin()
    app = App()
    app.mainloop()
