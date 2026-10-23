#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
무제한 전사기 (transcribe.py)
녹음·영상 파일을 길이 제한 없이 한국어 텍스트로 바꿉니다. 내 컴퓨터(또는 Colab)에서 직접 돌리므로
파일당 길이 제한, 월 사용량 제한, 업로드 용량 제한이 없습니다.

사용법
  python transcribe.py 녹음.m4a                    # 기본: 한국어, 모델은 GPU면 large-v3 / CPU면 large-v3-turbo
  python transcribe.py 녹음폴더/                   # 폴더 안 음성·영상 파일 일괄 처리
  python transcribe.py 강의.mp4 --model small      # 빠른 미리보기 (정확도는 낮음)
  python transcribe.py 인터뷰.mp3 --prompt "브랜드명, 인물명"   # 고유명사 힌트
  python transcribe.py 녹음.m4a --plan-only        # 모델 없이 분할 계획만 확인

출력 (기본: 입력 파일 옆 '전사결과' 폴더)
  이름.txt               문단 단위 본문 (블로그·노션에 바로 붙여넣기용)
  이름.타임스탬프.txt     [HH:MM:SS] 문장 단위 (검수·편집용)
  이름.srt               자막 파일 (영상 편집기·Vrew 등에서 사용)
  이름.json              원본 세그먼트 (시작·끝·텍스트·신뢰도)

중단돼도 괜찮습니다. 같은 명령을 다시 실행하면 완료된 구간은 건너뛰고 이어서 진행합니다.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

MEDIA_EXT = {
    ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".oga", ".opus", ".aac", ".wma", ".amr", ".3gp", ".aiff", ".aif",
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".wmv",
}
DEFAULT_PROMPT = "다음은 한국어 녹음을 받아쓴 내용입니다. 문장 끝에는 마침표를 찍고, 숫자와 고유명사는 정확하게 표기합니다."
# 무음·잡음 구간에서 Whisper가 자주 지어내는 문구. 신뢰도가 낮을 때만 제거합니다.
JUNK_PHRASES = [
    "시청해주셔서 감사합니다", "시청해 주셔서 감사합니다", "구독과 좋아요", "구독 좋아요", "다음 영상에서 만나요",
    "자막 제공", "자막은", "MBC 뉴스", "KBS 뉴스", "SBS 뉴스", "뉴스 데스크", "Thank you for watching", "Thanks for watching",
]
WORK_DIRNAME = ".작업중"
MIN_CHUNK_MIN = 3


class Cancelled(Exception):
    """사용자가 중단을 요청했을 때 (창 프로그램의 '중지' 버튼)."""


def _never():
    return False


CANCEL_CHECK = _never   # 창 프로그램이 교체합니다. True를 돌려주면 현재 구간을 버리고 멈춥니다.
HANGUL = re.compile(r"[\uAC00-\uD7A3\u3131-\u318E]")


def log(msg=""):
    print(msg, flush=True)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    tmp = Path(str(path) + ".part")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(path)  # 쓰다 말고 꺼져도 깨진 파일이 남지 않도록


def with_ext(base, ext):
    """base가 '회의 2024.10.05'처럼 점을 포함해도 뒤에 확장자만 붙입니다 (with_suffix는 점 뒤를 잘라먹음)."""
    base = Path(base)
    return base.parent / f"{base.name}{ext}"


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------
def get_ffmpeg():
    """PATH의 ffmpeg를 쓰고, 없으면 pip로 설치된 imageio-ffmpeg의 내장 바이너리를 씁니다."""
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg  # requirements.txt에 포함
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    log("[오류] ffmpeg를 찾을 수 없습니다. 아래 중 하나로 설치하세요.")
    log("  - pip install imageio-ffmpeg        (가장 쉬움, 파이썬 패키지로 설치)")
    log("  - Windows: winget install Gyan.FFmpeg   /  Mac: brew install ffmpeg")
    sys.exit(2)


def run(cmd):
    """ffmpeg 실행. 창 프로그램의 [중지]에 반응하도록 0.3초마다 취소 여부를 확인하고, 취소되면 ffmpeg를 끝냅니다."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    while True:
        try:
            out, _ = proc.communicate(timeout=0.3)
            return subprocess.CompletedProcess(cmd, proc.returncode, out, None)
        except subprocess.TimeoutExpired:
            if CANCEL_CHECK():
                proc.kill()
                proc.communicate()
                raise Cancelled("사용자 중단")


def media_duration(ffmpeg, path):
    r = run([ffmpeg, "-hide_banner", "-i", str(path)])
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stdout)
    if not m:
        raise RuntimeError(f"길이를 읽을 수 없습니다 (손상된 파일이거나 지원하지 않는 형식): {path}\n{r.stdout[-800:]}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def to_wav16k(ffmpeg, src, dst):
    """어떤 음성·영상이든 16kHz 모노 WAV로 통일합니다 (Whisper 입력 규격). 37시간 이하 파일까지 지원."""
    r = run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)])
    if r.returncode != 0 or not Path(dst).exists():
        raise RuntimeError(f"변환 실패: {src}\n{r.stdout[-800:]}")


def detect_silences(ffmpeg, wav, noise_db=-35, min_sec=0.5):
    """(무음 시작, 무음 끝) 목록. 분할 지점을 말 중간이 아닌 조용한 곳으로 잡기 위해 씁니다."""
    r = run([ffmpeg, "-hide_banner", "-nostats", "-i", str(wav),
             "-af", f"silencedetect=noise={noise_db}dB:d={min_sec}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", r.stdout)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", r.stdout)]
    sil, j = [], 0
    for s in starts:
        while j < len(ends) and ends[j] <= s:
            j += 1
        if j < len(ends):
            sil.append((s, ends[j]))
            j += 1
        else:
            break
    return sil


def plan_chunks(duration, chunk_sec, silences, search_sec=120, min_gap=60):
    """전체 길이를 chunk_sec 단위로 나누되, 각 경계를 ±search_sec 안의 가장 긴 무음 중앙으로 옮깁니다."""
    if duration <= chunk_sec * 1.25:
        return [(0.0, duration)]
    n = max(1, round(duration / chunk_sec))
    target = duration / n
    search_sec = min(search_sec, target / 3)
    min_gap = min(min_gap, target / 3)
    bounds = [0.0]
    for k in range(1, n):
        t = k * target
        cands = [(e - s, (s + e) / 2) for s, e in silences
                 if abs((s + e) / 2 - t) <= search_sec and (s + e) / 2 > bounds[-1] + min_gap]
        if cands:
            # 긴 무음을 우선하되, 목표 지점에서 멀어질수록 약간 감점
            cut = max(cands, key=lambda c: c[0] - 0.002 * abs(c[1] - t))[1]
        else:
            cut = t
        cut = max(cut, bounds[-1] + min_gap)  # 경계는 항상 앞으로만
        bounds.append(round(cut, 3))
    bounds.append(duration)
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1) if bounds[i + 1] > bounds[i]]


def cut_chunk(ffmpeg, wav, start, end, dst):
    r = run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(wav), "-c", "copy", str(dst)])
    if r.returncode != 0 or not Path(dst).exists():
        raise RuntimeError(f"구간 자르기 실패 ({start:.1f}~{end:.1f}초)\n{r.stdout[-800:]}")


# ---------------------------------------------------------------------------
# 음성 인식 엔진 (faster-whisper)
# ---------------------------------------------------------------------------
def prepare_nvidia_dlls():
    """Windows에서 pip로 설치한 NVIDIA 라이브러리(nvidia-cublas-cu12, nvidia-cudnn-cu12)를 찾을 수 있게 합니다."""
    if sys.platform != "win32":
        return
    try:
        import site
        for sp in set(site.getsitepackages() + [site.getusersitepackages()]):
            for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
                d = Path(sp) / sub
                if d.is_dir():
                    os.add_dll_directory(str(d))
                    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def pick_device(device):
    if device != "auto":
        return device
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def pick_compute(compute, device):
    if compute != "auto":
        return compute
    return "float16" if device == "cuda" else "int8"


def pick_model(model, device):
    """한국어는 large-v3가 turbo보다 정확하다는 보고가 여럿이라 GPU면 large-v3, CPU면 속도 때문에 large-v3-turbo."""
    if model != "auto":
        return model
    return "large-v3" if device == "cuda" else "large-v3-turbo"


def load_engine(model_name, device, compute, threads):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log("[오류] faster-whisper가 설치되어 있지 않습니다:  pip install -r requirements.txt")
        sys.exit(2)
    if device == "cuda":
        prepare_nvidia_dlls()
    kw = {"device": device, "compute_type": compute}
    if device == "cpu":
        kw["cpu_threads"] = threads or max(1, os.cpu_count() or 4)
    log(f"모델 불러오는 중: {model_name} (장치 {device}, 연산 {compute}) — 처음 한 번은 모델을 내려받아 몇 분 걸립니다.")
    try:
        return WhisperModel(model_name, **kw)
    except Exception as e:
        if device != "cuda":
            raise
        # GPU 라이브러리(cuDNN·CUDA) 문제로 못 띄우는 경우가 잦아서, 멈추지 않고 CPU로 계속 진행합니다.
        log(f"[안내] GPU로 모델을 띄우지 못했습니다 ({str(e).splitlines()[0][:120]})")
        log("       CPU(int8)로 대신 진행합니다. 느리지만 결과는 같습니다. GPU를 쓰려면 README의 'GPU 관련 오류' 항목을 보세요.")
        kw = {"device": "cpu", "compute_type": "int8", "cpu_threads": threads or max(1, os.cpu_count() or 4)}
        return WhisperModel(model_name, **kw)


def transcribe_chunk(model, wav_path, language, prompt, beam, offset, chunk_len, progress_every=120):
    """faster-whisper로 구간 하나를 전사하고, 전체 파일 기준 시각으로 보정해 돌려줍니다."""
    segments, _info = model.transcribe(
        str(wav_path),
        language=language,
        beam_size=beam,
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        condition_on_previous_text=False,      # 긴 파일에서 같은 문장이 반복되는 현상 방지
        initial_prompt=prompt,
        vad_filter=True,                       # 무음 구간은 아예 모델에 넣지 않음 (환각 감소, 속도 향상)
        vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 300},
        word_timestamps=False,
    )
    out, next_mark = [], progress_every
    for s in segments:
        if CANCEL_CHECK():
            raise Cancelled("사용자 중단")
        text = (s.text or "").strip()
        out.append({
            "start": round(offset + s.start, 2),
            "end": round(offset + s.end, 2),
            "text": text,
            "avg_logprob": round(float(s.avg_logprob), 3),
            "no_speech_prob": round(float(s.no_speech_prob), 3),
        })
        if s.end >= next_mark:
            log(f"      … {fmt_ts(offset + s.end)} 까지 진행 ({min(100, int(100 * s.end / max(chunk_len, 1)))}%)")
            next_mark += progress_every
    return out


# ---------------------------------------------------------------------------
# 정리 · 출력
# ---------------------------------------------------------------------------
def clean_segments(segs, language="ko"):
    """환각(지어낸 문장)과 반복을 보수적으로 제거합니다. 확신이 없으면 남깁니다."""
    out, prev_text, repeat = [], None, 0
    for s in segs:
        t = s["text"]
        if not t:
            continue
        if s["no_speech_prob"] > 0.85 and s["avg_logprob"] < -1.0:
            continue  # 무음인데 뭔가 적힌 경우
        if language == "ko" and s["avg_logprob"] < -0.8 and not HANGUL.search(t) and not re.search(r"\d", t):
            continue  # 한국어 녹음인데 한글도 숫자도 없고 확신도 낮음 → 다른 언어로 잘못 인식한 환각
        if s["no_speech_prob"] > 0.4 and any(j in t for j in JUNK_PHRASES):
            continue  # 유튜브 자막 흔적 같은 상투구
        if t == prev_text:
            repeat += 1
            if repeat >= 2:
                continue  # 같은 문장이 3번 이상 연속되면 이후는 버림
        else:
            repeat = 0
        prev_text = t
        out.append(s)
    return out


def fmt_ts(sec, srt=False):
    ms = max(0, int(round(float(sec) * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    if srt:
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def paragraphs(segs, gap=2.0, max_chars=500):
    """문장 사이 쉼이 gap초 이상이거나 문단이 max_chars를 넘으면 새 문단."""
    paras, cur, cur_len, last_end = [], [], 0, None
    for s in segs:
        if cur and ((s["start"] - last_end) >= gap or cur_len >= max_chars):
            paras.append(" ".join(cur))
            cur, cur_len = [], 0
        cur.append(s["text"])
        cur_len += len(s["text"])
        last_end = s["end"]
    if cur:
        paras.append(" ".join(cur))
    return paras


def write_outputs(segs, base, meta):
    with open(with_ext(base, ".json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "segments": segs}, f, ensure_ascii=False, indent=1)
    with open(with_ext(base, ".srt"), "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{fmt_ts(s['start'], True)} --> {fmt_ts(s['end'], True)}\n{s['text']}\n\n")
    with open(with_ext(base, ".타임스탬프.txt"), "w", encoding="utf-8") as f:
        for s in segs:
            f.write(f"[{fmt_ts(s['start'])}] {s['text']}\n")
    with open(with_ext(base, ".txt"), "w", encoding="utf-8") as f:
        f.write("\n\n".join(paragraphs(segs)) + "\n")


# ---------------------------------------------------------------------------
# 파일 하나 처리
# ---------------------------------------------------------------------------
class LazyEngine:
    def __init__(self, args):
        self.args, self.model = args, None

    def get(self):
        if self.model is None:
            a = self.args
            self.model = load_engine(a.model, a.device, a.compute, a.threads)
        return self.model

    def fallback_to_cpu(self):
        """GPU에서 첫 연산 중 실패(DLL 누락 등)하면 CPU로 갈아탑니다. 이후 파일도 CPU로 처리됩니다."""
        self.args.device, self.args.compute, self.model = "cpu", "int8", None
        return self.get()


def output_base(src, out_dir, overwrite):
    """결과 파일 이름. 보통은 확장자만 뗀 이름(강의). 같은 이름의 다른 파일(강의.mp4 vs 강의.m4a) 결과가
    이미 있으면 확장자까지 붙인 이름(강의.mp4)을 씁니다. 두 번째 값이 True면 '이미 같은 파일의 결과가 있음'."""
    candidates = [out_dir / src.stem, out_dir / src.name] + [out_dir / f"{src.name}_{k}" for k in range(2, 20)]
    for base in candidates:
        existing = with_ext(base, ".json")
        if not existing.exists():
            return base, False
        try:
            prev_source = read_json(existing).get("meta", {}).get("source")
        except Exception:
            prev_source = None
        if prev_source == src.name:
            return base, (not overwrite)
    return candidates[-1], False


def source_fingerprint(src, args):
    st = src.stat()
    return {
        "source": {"name": src.name, "size": st.st_size, "mtime": int(st.st_mtime)},
        "settings": {"model": args.model, "language": args.language, "prompt": args.prompt,
                     "beam": args.beam, "chunk_min": args.chunk_min},
    }


def process_file(src, args, ffmpeg, engine):
    src = Path(src)
    out_dir = Path(args.outdir) if args.outdir else src.parent / "전사결과"
    out_dir.mkdir(parents=True, exist_ok=True)
    base, skip = output_base(src, out_dir, args.overwrite)
    if skip:
        log(f"건너뜀 (이미 결과 있음, 다시 하려면 --overwrite): {src.name}")
        return

    work = out_dir / WORK_DIRNAME / src.name
    fp = source_fingerprint(src, args)
    plan_path = work / "chunks.json"
    plan = None
    if work.exists():
        try:
            plan = read_json(plan_path) if plan_path.exists() else None
        except Exception:
            plan = None
        if args.overwrite or not plan or plan.get("source") != fp["source"]:
            shutil.rmtree(work, ignore_errors=True)   # 원본이 바뀌었거나 다시 하라고 했으니 처음부터
            plan = None
        elif plan.get("settings") != fp["settings"]:
            log("   설정(모델·프롬프트 등)이 바뀌어 이전 구간 결과는 버리고 다시 전사합니다. (변환된 음성은 재사용)")
            for p in work.glob("chunk_*.json"):
                p.unlink()
            plan_path.unlink(missing_ok=True)
            plan = None
    work.mkdir(parents=True, exist_ok=True)

    wav = work / "audio16k.wav"
    log(f"\n=== {src.name} ===")
    if not wav.exists():
        if CANCEL_CHECK():
            raise Cancelled("사용자 중단")
        log("1) 16kHz 모노 WAV로 변환 중…")
        tmp = work / "audio16k.part.wav"
        try:
            to_wav16k(ffmpeg, src, tmp)
        except Cancelled:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(wav)
    duration = media_duration(ffmpeg, wav)

    if plan:
        chunks = [tuple(c) for c in plan["chunks"]]
    else:
        if CANCEL_CHECK():
            raise Cancelled("사용자 중단")
        log("2) 조용한 지점 찾아서 분할 계획 세우는 중…")
        silences = detect_silences(ffmpeg, wav)
        chunks = plan_chunks(duration, args.chunk_min * 60, silences)
        write_json(plan_path, dict(fp, chunks=chunks))
    log(f"   전체 {fmt_ts(duration)} → {len(chunks)}개 구간 (약 {args.chunk_min}분씩)")
    for i, (a, b) in enumerate(chunks, 1):
        log(f"   구간 {i:>3}: {fmt_ts(a)} ~ {fmt_ts(b)}")
    if args.plan_only:
        log("   --plan-only 지정: 여기서 멈춥니다. (변환된 음성은 다음 실행에서 재사용)")
        return

    if CANCEL_CHECK():
        raise Cancelled("사용자 중단")
    model = engine.get()   # 모델 내려받기 중에는 중지가 바로 반영되지 않습니다
    prompt = DEFAULT_PROMPT + (f" 주요 용어: {args.prompt}." if args.prompt else "")
    log("3) 전사 중…")
    all_segs, t0, done_sec = [], time.time(), 0.0
    for i, (a, b) in enumerate(chunks, 1):
        if CANCEL_CHECK():
            raise Cancelled("사용자 중단")
        cj = work / f"chunk_{i:03d}.json"
        if cj.exists():
            segs = read_json(cj)
            log(f"   [{i}/{len(chunks)}] 이미 완료된 구간 → 건너뜀")
        else:
            cw = work / f"chunk_{i:03d}.wav"
            if len(chunks) == 1:
                cw = wav
            elif not cw.exists():
                cut_chunk(ffmpeg, wav, a, b, cw)
            log(f"   [{i}/{len(chunks)}] {fmt_ts(a)} ~ {fmt_ts(b)} 전사 시작")
            try:
                segs = transcribe_chunk(model, cw, args.language, prompt, args.beam, a, b - a)
            except Exception as e:
                if args.device != "cuda":
                    raise
                log(f"[안내] GPU 연산 중 오류 ({str(e).splitlines()[0][:120]})")
                log("       CPU(int8)로 전환해 이어서 진행합니다. GPU를 쓰려면 README의 'GPU 관련 오류' 항목을 보세요.")
                if args.model == "large-v3":
                    log("       (CPU에서는 --model large-v3-turbo 가 훨씬 빠릅니다. 급하면 중단 후 그 옵션으로 다시 실행하세요.)")
                model = engine.fallback_to_cpu()
                segs = transcribe_chunk(model, cw, args.language, prompt, args.beam, a, b - a)
            write_json(cj, segs)
            if cw != wav:
                cw.unlink(missing_ok=True)
            done_sec += (b - a)
            elapsed = time.time() - t0
            speed = done_sec / max(elapsed, 1e-6)
            remain = sum(bb - aa for aa, bb in chunks[i:])
            log(f"   [{i}/{len(chunks)}] 완료 · 처리 속도 {speed:.1f}배속 · 남은 예상 {fmt_ts(remain / max(speed, 1e-6))}")
        all_segs.extend(segs)

    all_segs.sort(key=lambda s: s["start"])
    segs = clean_segments(all_segs, args.language)
    meta = {
        "source": src.name, "duration_sec": round(duration, 2), "model": args.model, "language": args.language,
        "device": args.device, "prompt": args.prompt, "chunks": len(chunks),
        "segments_raw": len(all_segs), "segments_kept": len(segs),
        "generated_by": "transcribe.py", "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_outputs(segs, base, meta)
    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
        parent = out_dir / WORK_DIRNAME
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    log(f"4) 완료 → {with_ext(base, '.txt')}  (+ .타임스탬프.txt, .srt, .json)")


def collect_inputs(paths):
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files += sorted(x for x in p.iterdir() if x.suffix.lower() in MEDIA_EXT and x.is_file())
        elif p.is_file():
            files.append(p)
        else:
            log(f"[경고] 찾을 수 없음: {p}")
    return files


def build_parser():
    ap = argparse.ArgumentParser(description="녹음·영상 파일을 길이 제한 없이 한국어 텍스트로 전사합니다.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("inputs", nargs="+", help="음성/영상 파일 또는 폴더 (여러 개 가능)")
    ap.add_argument("--model", default="auto",
                    help="auto(기본): GPU면 large-v3, CPU면 large-v3-turbo. 직접 지정: tiny / base / small / medium / large-v3 / large-v3-turbo 또는 모델 폴더 경로")
    ap.add_argument("--language", default="ko", help="언어 코드 (기본 ko). 자동 감지는 auto")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"], help="auto(기본): GPU 있으면 GPU")
    ap.add_argument("--compute", default="auto", help="auto(기본) / int8 / float16 / int8_float16 / float32")
    ap.add_argument("--threads", type=int, default=0, help="CPU 스레드 수 (0=자동)")
    ap.add_argument("--chunk-min", type=int, default=20, help=f"구간 길이(분). 기본 20, 최소 {MIN_CHUNK_MIN}. 메모리가 적으면 10")
    ap.add_argument("--beam", type=int, default=5, help="빔 크기. 빠르게 하려면 1~2")
    ap.add_argument("--prompt", default="", help="고유명사·전문용어 힌트 (쉼표로 구분)")
    ap.add_argument("--outdir", default="", help="결과 폴더 (기본: 입력 파일 옆 '전사결과')")
    ap.add_argument("--overwrite", action="store_true", help="결과·중간 파일이 있어도 처음부터 다시 전사")
    ap.add_argument("--keep-work", action="store_true", help="중간 파일(.작업중) 삭제하지 않음")
    ap.add_argument("--plan-only", action="store_true", help="모델을 띄우지 않고 변환·분할 계획만 확인")
    return ap


def main(argv=None):
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.chunk_min < MIN_CHUNK_MIN:
        ap.error(f"--chunk-min 은 {MIN_CHUNK_MIN} 이상이어야 합니다.")
    if args.beam < 1:
        ap.error("--beam 은 1 이상이어야 합니다.")
    if args.language.lower() == "auto":
        args.language = None
    args.device = pick_device(args.device)
    args.compute = pick_compute(args.compute, args.device)
    args.model = pick_model(args.model, args.device)

    files = collect_inputs(args.inputs)
    if not files:
        log("처리할 파일이 없습니다.")
        return 1
    ffmpeg = get_ffmpeg()
    engine = LazyEngine(args)
    log(f"대상 파일 {len(files)}개 · 모델 {args.model} · 장치 {args.device}")
    failed = []
    for f in files:
        try:
            process_file(f, args, ffmpeg, engine)
        except (KeyboardInterrupt, Cancelled):
            log("\n중단됨. 같은 명령을 다시 실행하면 이어서 진행합니다.")
            return 130
        except Exception as e:  # 한 파일이 실패해도 나머지는 계속
            failed.append((f.name, str(e)))
            log(f"[실패] {f.name}: {e}")
    if failed:
        log(f"\n실패 {len(failed)}건: " + ", ".join(n for n, _ in failed))
        return 1
    log("\n모두 완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
