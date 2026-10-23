#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
무제한 전사기 — 창(GUI) 버전
더블클릭으로 열어서 파일을 고르고 [전사 시작]을 누르면 됩니다. 엔진은 transcribe.py와 같습니다.
"""

import os
import queue
import subprocess
import sys
import threading
import traceback
import types
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcribe as T  # noqa: E402

APP_TITLE = "무제한 전사기"
MODEL_CHOICES = [
    ("자동 (권장: GPU면 large-v3, CPU면 large-v3-turbo)", "auto"),
    ("large-v3 (가장 정확, 느림)", "large-v3"),
    ("large-v3-turbo (빠름, 정확도 약간 낮음)", "large-v3-turbo"),
    ("medium", "medium"),
    ("small (빠른 미리보기용, 부정확)", "small"),
]
LANG_CHOICES = [("한국어", "ko"), ("영어", "en"), ("자동 감지 (언어가 섞인 녹음)", "auto")]
FILETYPES = [("음성·영상 파일", " ".join(f"*{e}" for e in sorted(T.MEDIA_EXT))), ("모든 파일", "*.*")]


def build_args(model="auto", language="ko", prompt="", outdir="", overwrite=False, chunk_min=20, beam=5):
    """transcribe.py의 argparse 결과와 같은 모양의 설정 객체."""
    a = types.SimpleNamespace(
        model=model, language=(None if language == "auto" else language), device="auto", compute="auto", threads=0,
        chunk_min=chunk_min, beam=beam, prompt=prompt.strip(), outdir=outdir.strip().strip('"').strip("'").strip(), overwrite=overwrite,
        keep_work=False, plan_only=False,
    )
    a.device = T.pick_device(a.device)
    a.compute = T.pick_compute(a.compute, a.device)
    a.model = T.pick_model(a.model, a.device)
    return a


def run_jobs(files, args, emit, cancel_check):
    """백그라운드 스레드에서 실행. emit(kind, payload)로 창에 알리고, cancel_check()가 True면 멈춥니다."""
    T.log = lambda msg="": emit("log", str(msg))
    T.CANCEL_CHECK = cancel_check
    done, failed, cancelled = 0, [], False
    try:
        ffmpeg = T.get_ffmpeg()
        engine = T.LazyEngine(args)
        emit("log", f"대상 파일 {len(files)}개 · 모델 {args.model} · 장치 {'GPU' if args.device == 'cuda' else 'CPU'}")
        for i, f in enumerate(files, 1):
            if cancel_check():
                cancelled = True
                break
            emit("progress", (done, len(files), f.name))
            try:
                T.process_file(f, args, ffmpeg, engine)
                done += 1
                emit("outdir", str(Path(args.outdir) if args.outdir else f.parent / "전사결과"))
            except T.Cancelled:
                cancelled = True
                emit("log", "\n중단됨. 다시 [전사 시작]을 누르면 완료된 구간은 건너뛰고 이어서 진행합니다.")
                break
            except SystemExit as e:  # faster-whisper 미설치 등: transcribe.py가 sys.exit 하는 경우
                failed.append((f.name, f"설치 문제 (코드 {e.code})"))
                break
            except Exception as e:
                failed.append((f.name, str(e)))
                emit("log", f"[실패] {f.name}: {e}")
                emit("log", traceback.format_exc(limit=3))
            emit("progress", (done, len(files), f.name))
    except SystemExit as e:
        failed.append(("(시작 전)", f"설치 문제 (코드 {e.code})"))
    except Exception as e:
        failed.append(("(시작 전)", str(e)))
        emit("log", traceback.format_exc(limit=3))
    emit("done", {"done": done, "failed": failed, "cancelled": cancelled, "total": len(files)})


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.scale = max(1.0, self.winfo_fpixels("1i") / 96.0)   # 고해상도(125%, 150%…) 화면 배율
        self.files = []
        self.q = queue.Queue()
        self.worker = None
        self.cancel = threading.Event()
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
        self.status_var = tk.StringVar(value=f"준비됨 · 장치: {'GPU' if T.pick_device('auto') == 'cuda' else 'CPU'}")
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

    def current_args(self):
        model = dict(MODEL_CHOICES)[self.model_var.get()]
        lang = dict(LANG_CHOICES)[self.lang_var.get()]
        return build_args(model=model, language=lang, prompt=self.prompt_var.get(),
                          outdir=self.outdir_var.get(), overwrite=self.overwrite_var.get())

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showinfo(APP_TITLE, "먼저 파일을 추가하세요.")
            return
        args = self.current_args()
        self.last_outdir = Path(args.outdir) if args.outdir else self.files[0].parent / "전사결과"
        self.cancel.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress.configure(maximum=len(self.files), value=0)
        self.status_var.set("전사 중…")
        files = list(self.files)
        self.worker = threading.Thread(target=run_jobs, args=(files, args, self._emit, self.cancel.is_set), daemon=True)
        self.worker.start()

    def stop(self):
        if self.worker and self.worker.is_alive():
            self.cancel.set()
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
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_TITLE, "전사가 진행 중입니다. 창을 닫으면 현재 구간은 버려지고, 다음에 이어서 할 수 있습니다. 닫을까요?"):
                return
            self.cancel.set()
            self.worker.join(3)   # ffmpeg 자식 프로세스가 정리될 시간
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
