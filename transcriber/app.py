#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
무제한 전사기 — 창(GUI) 버전
더블클릭으로 열어서 파일을 고르고 [전사 시작]을 누르면 됩니다.
전사 자체는 transcribe.py를 별도 프로세스로 띄워서 합니다. 음성인식 엔진(C++ 라이브러리)이 오류 메시지 없이
프로세스를 통째로 끝내는 일이 있는데, 같은 프로세스 안에서 돌리면 창까지 사라지기 때문입니다.
그렇게 죽으면 창은 남아 있고, 완료된 구간은 저장돼 있으므로 자동으로 이어서 다시 시작합니다.
"""

import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import transcribe as T  # noqa: E402  (파일 목록·장치 확인에만 씀. 전사는 별도 프로세스)

APP_TITLE = "무제한 전사기"
ENGINE_SCRIPT = HERE / "transcribe.py"
CRASH_RETRY_MAX = 3      # 진전 없이 이 횟수만큼 다시 시작해도 또 죽으면 포기
CRASH_RETRY_WAIT = 10    # 초. 다시 시작하기 전 대기 (GPU·메모리가 정리될 시간)
STOP_KILL_AFTER = 20     # 초. [중지] 뒤 이 시간 안에 스스로 안 끝나면(모델 내려받기 중 등) 강제 종료
MODEL_CHOICES = [
    ("자동 (권장: GPU면 large-v3, CPU면 large-v3-turbo)", "auto"),
    ("large-v3 (가장 정확, 느림)", "large-v3"),
    ("large-v3-turbo (빠름, 정확도 약간 낮음)", "large-v3-turbo"),
    ("medium", "medium"),
    ("small (빠른 미리보기용, 부정확)", "small"),
]
LANG_CHOICES = [("한국어", "ko"), ("영어", "en"), ("자동 감지 (언어가 섞인 녹음)", "auto")]
FILETYPES = [("음성·영상 파일", " ".join(f"*{e}" for e in sorted(T.MEDIA_EXT))), ("모든 파일", "*.*")]


def build_cmd(files, opts, cancel_file):
    """transcribe.py 명령줄. 모델·언어의 '자동'은 그대로 넘겨, 장치에 맞는 선택을 transcribe.py가 하게 합니다."""
    cmd = [sys.executable, "-u", str(ENGINE_SCRIPT), *[str(f) for f in files],
           "--model", opts["model"], "--language", opts["language"], "--cancel-file", str(cancel_file)]
    if opts["prompt"]:
        cmd += ["--prompt", opts["prompt"]]
    if opts["outdir"]:
        cmd += ["--outdir", opts["outdir"]]
    if opts["overwrite"]:
        cmd.append("--overwrite")
    return cmd


def start_engine(cmd):
    """출력을 줄 단위로 받도록 파이프로 띄웁니다. Windows는 콘솔 창이 새로 뜨지 않게, Mac/Linux는 ffmpeg까지 한 번에 끝낼 수 있게 프로세스 그룹으로."""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1, env=env, **kw)


def kill_tree(proc):
    """엔진과 그 아래에서 돌고 있는 ffmpeg까지 강제 종료합니다."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class Job:
    """[전사 시작] 한 번. 백그라운드 스레드에서 transcribe.py를 별도 프로세스로 돌리고 출력을 창에 전달합니다.

    종료 코드 0(완료)·1(파일 문제로 실패)·130(중단)은 transcribe.py가 스스로 끝낸 것이고, 그 외는 엔진이
    메시지 없이 죽은 것입니다. 그러면 몇 초 뒤 아직 안 끝난 파일로 같은 설정으로 다시 시작합니다.
    완료된 구간은 transcribe.py가 저장해 두므로 이어서 진행됩니다. 구간이 하나라도 새로 끝났으면 재시작 횟수를
    다시 세고, 진전 없이 CRASH_RETRY_MAX번 다시 시작해도 죽으면 포기합니다 (같은 구간에서 반복되는 문제).
    """

    def __init__(self, files, opts, emit):
        self.files, self.opts, self.emit = list(files), opts, emit
        self.cancel = threading.Event()
        self.proc = None
        self.cancel_file = Path(tempfile.gettempdir()) / f"transcriber-cancel-{os.getpid()}-{int(time.time())}"
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def is_alive(self):
        return self.thread.is_alive()

    def request_stop(self, hard=False):
        """[중지]: 취소 파일을 만들어 transcribe.py가 현재 구간을 버리고 스스로 끝내게 합니다(코드 130).
        시간 안에 안 끝나면 강제 종료. hard=True(창 닫기)는 기다리지 않고 바로 강제 종료."""
        self.cancel.set()
        try:
            self.cancel_file.touch()
        except Exception:
            pass
        proc = self.proc
        if proc is None:
            return
        if hard:
            kill_tree(proc)
        else:
            t = threading.Timer(STOP_KILL_AFTER, kill_tree, [proc])
            t.daemon = True
            t.start()

    def _run(self):
        done, failed, current = {}, {}, None   # 파일 이름 → 결과. 다시 시작할 때 같은 파일이 두 번 집계되지 않게 dict
        crashes, cancelled, overwrite = 0, False, self.opts["overwrite"]
        remaining = list(self.files)
        try:
            while remaining:
                self.cancel_file.unlink(missing_ok=True)
                self.proc = start_engine(build_cmd(remaining, dict(self.opts, overwrite=overwrite), self.cancel_file))
                progressed = False
                for line in self.proc.stdout:
                    line = line.rstrip("\r\n")
                    self.emit("log", line)
                    if line.startswith("=== ") and line.endswith(" ==="):
                        current = line[4:-4]
                        self.emit("progress", (len(done), len(self.files), current))
                    elif line.startswith("4) 완료 →"):
                        done[current] = True
                        progressed = True
                        try:
                            self.emit("outdir", str(Path(line.split("→", 1)[1].split("  (+")[0].strip()).parent))
                        except Exception:
                            pass
                        self.emit("progress", (len(done), len(self.files), current))
                    elif line.startswith("건너뜀 ("):
                        name = line.rsplit(": ", 1)[-1]
                        done[name] = True
                        self.emit("progress", (len(done), len(self.files), name))
                    elif "완료 · 처리 속도" in line:
                        progressed = True     # 구간 하나가 저장됨 → 다음에 죽으면 재시작 횟수를 새로 셉니다
                    elif line.startswith("[실패] "):
                        name, _, err = line[len("[실패] "):].partition(": ")
                        failed[name] = err
                code = self.proc.wait()
                if self.cancel.is_set():
                    cancelled = True
                    break
                if code in (0, 1, 130):       # transcribe.py가 스스로 끝낸 경우. 할 말은 이미 출력했음
                    cancelled = code == 130
                    break
                # 여기부터는 엔진이 메시지 없이 죽은 경우
                crashes = 1 if progressed else crashes + 1
                remaining = [f for f in self.files if f.name not in done and f.name not in failed]
                overwrite = False     # '처음부터 다시'를 재시작 때 또 적용하면 저장된 구간을 버리므로 끕니다
                if crashes > CRASH_RETRY_MAX or not remaining:
                    self.emit("log", f"\n[실패] 엔진이 계속 강제 종료됩니다 (코드 {code}). {CRASH_RETRY_MAX}번 다시 시작했지만 진전이 없어 멈춥니다. "
                                     "완료된 구간은 저장되어 있습니다. 같은 구간에서 반복되면 다른 모델(large-v3-turbo·medium)로 시도해 보세요.")
                    if current:
                        failed.setdefault(current, f"엔진 강제 종료 반복 (코드 {code})")
                    break
                self.emit("log", f"\n[안내] 엔진이 예기치 않게 종료됐습니다 (코드 {code}). 완료된 구간은 저장되어 있으니 "
                                 f"{CRASH_RETRY_WAIT}초 후 이어서 다시 시작합니다. ({crashes}/{CRASH_RETRY_MAX})")
                self.emit("status", f"엔진 다시 시작 대기 중… ({crashes}/{CRASH_RETRY_MAX})")
                for _ in range(CRASH_RETRY_WAIT * 2):
                    if self.cancel.is_set():
                        break
                    time.sleep(0.5)
                if self.cancel.is_set():
                    cancelled = True
                    break
        except Exception as e:      # 엔진을 띄우지 못한 경우 등
            self.emit("log", f"[실패] 엔진을 실행할 수 없습니다: {e}")
            failed.setdefault("(시작 전)", str(e))
        finally:
            self.cancel_file.unlink(missing_ok=True)
        if cancelled:
            self.emit("log", "\n중단됨. 다시 [전사 시작]을 누르면 완료된 구간은 건너뛰고 이어서 진행합니다.")
        self.emit("done", {"done": len(done), "failed": list(failed.items()), "cancelled": cancelled, "total": len(self.files)})


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.scale = max(1.0, self.winfo_fpixels("1i") / 96.0)   # 고해상도(125%, 150%…) 화면 배율
        self.files = []
        self.q = queue.Queue()
        self.job = None
        self.last_outdir = None
        self._build()
        # 창 크기는 배율에 맞춰 내용이 다 보이는 크기로 (고정 픽셀값을 쓰면 150% 화면에서 아래가 잘림)
        self.update_idletasks()
        w = max(self.winfo_reqwidth(), int(780 * self.scale))
        h = max(self.winfo_reqheight(), int(660 * self.scale))
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())
        self.geometry(f"{w}x{h}")
        self.after(100, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- UI
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="녹음·영상 파일을 길이 제한 없이 한국어 텍스트로 바꿉니다. 결과는 파일 옆 '전사결과' 폴더에 저장됩니다.",
                  wraplength=int(740 * self.scale)).pack(anchor="w")

        files_box = ttk.LabelFrame(self, text="1. 파일")
        files_box.pack(fill="both", expand=False, **pad)
        btns = ttk.Frame(files_box)
        btns.pack(fill="x", padx=6, pady=4)
        ttk.Button(btns, text="파일 추가…", command=self.add_files).pack(side="left")
        ttk.Button(btns, text="폴더 추가…", command=self.add_folder).pack(side="left", padx=4)
        ttk.Button(btns, text="선택 제거", command=self.remove_selected).pack(side="left")
        ttk.Button(btns, text="목록 비우기", command=self.clear_files).pack(side="left", padx=4)
        self.count_var = tk.StringVar(value="0개")
        ttk.Label(btns, textvariable=self.count_var).pack(side="right")
        lb_frame = ttk.Frame(files_box)
        lb_frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.listbox = tk.Listbox(lb_frame, height=6, selectmode="extended", font="TkDefaultFont")
        sb = ttk.Scrollbar(lb_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        opt = ttk.LabelFrame(self, text="2. 설정 (보통 그대로 두면 됩니다)")
        opt.pack(fill="x", **pad)
        opt.columnconfigure(1, weight=1)
        ttk.Label(opt, text="모델").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.model_var = tk.StringVar(value=MODEL_CHOICES[0][0])
        ttk.Combobox(opt, textvariable=self.model_var, values=[m[0] for m in MODEL_CHOICES], state="readonly").grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(opt, text="언어").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.lang_var = tk.StringVar(value=LANG_CHOICES[0][0])
        ttk.Combobox(opt, textvariable=self.lang_var, values=[l[0] for l in LANG_CHOICES], state="readonly").grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Label(opt, text="고유명사 힌트").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self.prompt_var = tk.StringVar()
        ttk.Entry(opt, textvariable=self.prompt_var).grid(row=2, column=1, sticky="ew", padx=6)
        ttk.Label(opt, text="(사람·브랜드·제품·용어를 쉼표로. 오인식이 크게 줄어듭니다)").grid(row=3, column=1, sticky="w", padx=6)
        ttk.Label(opt, text="결과 폴더").grid(row=4, column=0, sticky="w", padx=6, pady=3)
        out_row = ttk.Frame(opt)
        out_row.grid(row=4, column=1, sticky="ew", padx=6)
        out_row.columnconfigure(0, weight=1)
        self.outdir_var = tk.StringVar()
        ttk.Entry(out_row, textvariable=self.outdir_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(out_row, text="찾아보기…", command=self.choose_outdir).grid(row=0, column=1, padx=(4, 0))
        ttk.Label(opt, text="(비워 두면 각 파일 옆 '전사결과' 폴더)").grid(row=5, column=1, sticky="w", padx=6)
        self.overwrite_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="이미 결과가 있어도 처음부터 다시 전사", variable=self.overwrite_var).grid(row=6, column=1, sticky="w", padx=6, pady=(2, 6))

        run_box = ttk.LabelFrame(self, text="3. 실행")
        run_box.pack(fill="both", expand=True, **pad)
        row = ttk.Frame(run_box)
        row.pack(fill="x", padx=6, pady=4)
        self.start_btn = ttk.Button(row, text="전사 시작", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(row, text="중지", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        ttk.Button(row, text="결과 폴더 열기", command=self.open_outdir).pack(side="left")
        device = T.pick_device("auto")
        self.status_var = tk.StringVar(value=f"준비됨 · 장치: {'GPU' if device == 'cuda' else 'CPU'}" + (" (GPU 라이브러리 없음)" if T.DEVICE_NOTE else ""))
        ttk.Label(row, textvariable=self.status_var).pack(side="right")
        self.progress = ttk.Progressbar(run_box, mode="determinate", maximum=1, value=0)
        self.progress.pack(fill="x", padx=6, pady=(0, 4))
        log_frame = ttk.Frame(run_box)
        log_frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.log = tk.Text(log_frame, height=12, wrap="word", state="disabled", font="TkDefaultFont")  # 고정폭 대신 기본 UI 글꼴: 한글이 잘 보임
        lsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        self._append_log("파일을 추가하고 [전사 시작]을 누르세요. 처음 한 번은 모델(1.5~3GB)을 내려받느라 몇 분 걸립니다.")
        if T.DEVICE_NOTE:
            self._append_log(f"[안내] {T.DEVICE_NOTE}")

    # ------------------------------------------------------------ actions
    def _set_files(self, files):
        seen, uniq = set(), []
        for f in files:
            f = Path(f)
            if f not in seen and f.is_file():
                seen.add(f)
                uniq.append(f)
        self.files = uniq
        self.listbox.delete(0, "end")
        for f in self.files:
            self.listbox.insert("end", str(f))
        self.count_var.set(f"{len(self.files)}개")

    def add_files(self):
        paths = filedialog.askopenfilenames(title="음성·영상 파일 선택", filetypes=FILETYPES)
        if paths:
            self._set_files(self.files + [Path(p) for p in paths])

    def add_folder(self):
        d = filedialog.askdirectory(title="폴더 선택 (안의 음성·영상 파일을 모두 추가)")
        if d:
            found = T.collect_inputs([d])
            if not found:
                messagebox.showinfo(APP_TITLE, "이 폴더에는 음성·영상 파일이 없습니다.")
            self._set_files(self.files + found)

    def remove_selected(self):
        sel = set(self.listbox.curselection())
        self._set_files([f for i, f in enumerate(self.files) if i not in sel])

    def clear_files(self):
        self._set_files([])

    def choose_outdir(self):
        d = filedialog.askdirectory(title="결과 폴더 선택")
        if d:
            self.outdir_var.set(d)

    def current_opts(self):
        """transcribe.py에 넘길 설정. 모델·언어의 '자동'은 그대로 넘겨 장치에 맞는 선택을 transcribe.py가 합니다."""
        return {
            "model": dict(MODEL_CHOICES)[self.model_var.get()],
            "language": dict(LANG_CHOICES)[self.lang_var.get()],
            "prompt": self.prompt_var.get().strip(),
            "outdir": self.outdir_var.get().strip().strip('"').strip("'").strip(),
            "overwrite": self.overwrite_var.get(),
        }

    def start(self):
        if self.job and self.job.is_alive():
            return
        if not self.files:
            messagebox.showinfo(APP_TITLE, "먼저 파일을 추가하세요.")
            return
        opts = self.current_opts()
        self.last_outdir = Path(opts["outdir"]) if opts["outdir"] else self.files[0].parent / "전사결과"
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress.configure(maximum=len(self.files), value=0)
        self.status_var.set("전사 중…")
        self.job = Job(self.files, opts, self._emit)
        self.job.start()

    def stop(self):
        if self.job and self.job.is_alive():
            self.job.request_stop()
            self.stop_btn.configure(state="disabled")
            self.status_var.set("중단 요청… 현재 구간을 멈추는 중")

    def open_outdir(self):
        target = self.last_outdir
        if target is None and self.files:
            target = Path(self.outdir_var.get()) if self.outdir_var.get().strip() else self.files[0].parent / "전사결과"
        if target is None or not Path(target).exists():
            messagebox.showinfo(APP_TITLE, "아직 결과 폴더가 없습니다. 전사를 먼저 실행하세요.")
            return
        target = str(target)
        try:
            if sys.platform == "win32":
                os.startfile(target)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as e:
            messagebox.showinfo(APP_TITLE, f"폴더를 열 수 없습니다: {target}\n{e}")

    # ----------------------------------------------------- thread plumbing
    def _emit(self, kind, payload):
        self.q.put((kind, payload))

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip("\n") + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    done, total, name = payload
                    self.progress.configure(maximum=max(total, 1), value=done)
                    self.status_var.set(f"{done}/{total} 완료 · 현재: {name}")
                elif kind == "outdir":
                    self.last_outdir = Path(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass
        self.after(150, self._drain)

    def _finish(self, r):
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.progress.configure(value=r["done"])
        if r["cancelled"]:
            self.status_var.set(f"중단됨 · {r['done']}/{r['total']} 완료")
            if self.overwrite_var.get():
                self.overwrite_var.set(False)
                self._append_log("(이어서 하기 위해 '처음부터 다시 전사' 체크를 해제했습니다.)")
        elif r["failed"]:
            self.status_var.set(f"완료 (실패 {len(r['failed'])}건) · {r['done']}/{r['total']}")
            messagebox.showwarning(APP_TITLE, "일부 파일이 실패했습니다:\n" + "\n".join(f"- {n}: {e[:120]}" for n, e in r["failed"]))
        else:
            self.status_var.set(f"모두 완료 · {r['done']}/{r['total']}")
            self._append_log("\n모두 완료. [결과 폴더 열기]를 누르세요.")

    def _on_close(self):
        if self.job and self.job.is_alive():
            if not messagebox.askyesno(APP_TITLE, "전사가 진행 중입니다. 창을 닫으면 현재 구간은 버려지고, 다음에 이어서 할 수 있습니다. 닫을까요?"):
                return
            self.job.request_stop(hard=True)   # 엔진과 ffmpeg까지 바로 끝냄
            self.job.thread.join(3)
        self.destroy()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 고해상도 화면에서 글자 흐림 방지
        except Exception:
            pass
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
