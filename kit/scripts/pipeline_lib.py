#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline_lib.py —— 流水线校验公共库

被 validate_pipeline.py 与 check_visual.py 共用，提供三类能力：
  1. 文件指纹与媒体探针（sha256 / ffprobe）
  2. 画面可量化指标（亮度、对比度、饱和度、色温估计、主色板、色差 ΔE）
  3. 跨镜头一致性（色相直方图 χ² 距离）

设计原则：所有画面指标都作用在「已抽样、已按 alpha 过滤」的像素数组 (N,3) uint8 上，
避免把 1080x1920 全图留在内存里反复算。
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


# --------------------------------------------------------------------------
# 0. 外部二进制解析（ffmpeg / ffprobe）
# --------------------------------------------------------------------------
#
# 现实问题：一台机器上常同时存在多个 ffmpeg。如果 PATH 里排第一的是「精简构建」
# （只 --enable 了少量解复用器/滤镜，体积小、启动快），shutil.which() 会把我们
# 引向它，于是出现「命令明明在、校验却悄悄失效」：
#   - ffprobe 读不了 png / wav  → 图片、音频的 media 字段全部回填失败
#   - ffmpeg 写不出 png 帧      → 视频抽帧静默返回空列表
# 因此这里不信任 which 的第一个命中，而是「遍历整条 PATH + 能力自检」，
# 只挑真正具备所需能力的构建。可用环境变量强制指定：
#   PIPELINE_FFMPEG / PIPELINE_FFPROBE

_BIN_CACHE: dict[str, str | None] = {}
_PROBE_DIR: Path | None = None


def _tiny_png(w: int = 8, h: int = 8, rgb: tuple = (18, 52, 86)) -> bytes:
    """纯标准库造一张 8x8 PNG，作为能力自检样本（不依赖 Pillow）。"""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _tiny_wav(seconds: float = 0.2) -> bytes:
    """纯标准库造一段 220Hz 正弦 WAV，作为能力自检样本。"""
    import io
    import math
    import wave

    rate = 44100
    n = int(rate * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 220.0 * i / rate)))
            for i in range(n)
        ))
    return buf.getvalue()


def _probe_dir() -> Path:
    """自检样本只造一次，进程退出时清理。"""
    global _PROBE_DIR
    if _PROBE_DIR is None:
        _PROBE_DIR = Path(tempfile.mkdtemp(prefix="plib_probe_"))
        (_PROBE_DIR / "t.png").write_bytes(_tiny_png())
        (_PROBE_DIR / "t.wav").write_bytes(_tiny_wav())
        atexit.register(shutil.rmtree, _PROBE_DIR, ignore_errors=True)
    return _PROBE_DIR


def _candidates(name: str) -> list[str]:
    """按优先级列出候选可执行文件（已剔除不存在的路径）。

    顺序：环境变量 → 整条 PATH → imageio-ffmpeg 自带构建。
    注意 PATH 必须整条遍历：which() 只返回第一个命中，而第一个未必能用。
    """
    out: list[str] = []

    def add(p) -> None:
        if not p:
            return
        p = str(p)
        if p not in out and Path(p).is_file():
            out.append(p)

    add(os.environ.get(f"PIPELINE_{name.upper()}"))

    suffixes = [".exe", ""] if os.name == "nt" else [""]
    for directory in (os.environ.get("PATH") or "").split(os.pathsep):
        if not directory:
            continue
        for suffix in suffixes:
            add(Path(directory) / (name + suffix))

    try:  # imageio-ffmpeg 自带完整构建（仅 ffmpeg；同目录若带 ffprobe 也一并考虑）
        import imageio_ffmpeg  # type: ignore

        exe = Path(imageio_ffmpeg.get_ffmpeg_exe())
        add(exe if name == "ffmpeg" else exe.with_name(name + exe.suffix))
    except Exception:  # noqa: BLE001
        pass

    return out


def _capable_ffprobe(binary: str) -> bool:
    """能同时读出 PNG 与 WAV 的流信息，才算可用（对应 media 回填的真实需求）。"""
    probe = _probe_dir()
    for sample in ("t.png", "t.wav"):
        try:
            out = subprocess.run(
                [binary, "-v", "error", "-print_format", "json", "-show_streams",
                 str(probe / sample)],
                capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        if out.returncode != 0:
            return False
        try:
            streams = json.loads(out.stdout or "{}").get("streams") or []
        except json.JSONDecodeError:
            return False
        if not streams:
            return False
    return True


def _capable_ffmpeg(binary: str) -> bool:
    """能写 png 帧 + 能跑 loudnorm，才算可用（对应抽帧与响度分析的真实需求）。"""
    probe = _probe_dir()
    out_png = probe / "frame.png"
    if out_png.exists():
        out_png.unlink()
    try:
        r1 = subprocess.run(
            [binary, "-y", "-v", "error", "-loop", "1", "-i", str(probe / "t.png"),
             "-frames:v", "1", str(out_png)],
            capture_output=True, text=True, timeout=60)
        if r1.returncode != 0 or not out_png.exists():
            return False
    except (OSError, subprocess.SubprocessError):
        return False
    try:
        r2 = subprocess.run(
            [binary, "-hide_banner", "-nostats", "-f", "lavfi",
             "-i", "sine=frequency=220:duration=0.2",
             "-af", "loudnorm=print_format=json", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r2.returncode == 0 and "input_i" in ((r2.stdout or "") + (r2.stderr or ""))


def _resolve(name: str) -> str | None:
    if name in _BIN_CACHE:
        return _BIN_CACHE[name]
    check = _capable_ffprobe if name == "ffprobe" else _capable_ffmpeg
    cands = _candidates(name)
    chosen = next((c for c in cands if check(c)), None)
    if chosen is None and cands:
        chosen = cands[0]
        eprint(f"[pipeline_lib] 警告：{name} 只找到功能受限的构建（{chosen}），"
               f"抽帧或响度分析可能缺失；可用环境变量 PIPELINE_{name.upper()} 指定完整版。")
    if chosen is None:
        eprint(f"[pipeline_lib] 警告：未找到可用的 {name}，依赖它的校验项会被跳过。")
    _BIN_CACHE[name] = chosen
    return chosen


def ffmpeg_bin() -> str | None:
    """可用 ffmpeg 的绝对路径（首次调用做能力自检，之后走缓存）。"""
    return _resolve("ffmpeg")


def ffprobe_bin() -> str | None:
    """可用 ffprobe 的绝对路径（首次调用做能力自检，之后走缓存）。"""
    return _resolve("ffprobe")


def have(binary: str) -> bool:
    """ffmpeg / ffprobe 走能力自检；其它命令直接查 PATH。"""
    if binary in ("ffmpeg", "ffprobe"):
        return _resolve(binary) is not None
    return shutil.which(binary) is not None


def bins_report() -> dict:
    """把最终选中的二进制暴露出来，便于随报告一起输出、排查环境问题。"""
    return {"ffmpeg": ffmpeg_bin(), "ffprobe": ffprobe_bin()}


# --------------------------------------------------------------------------
# 1. 文件指纹 & 媒体探针
# --------------------------------------------------------------------------

def sha256_file(path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _eval_ratio(text) -> float | None:
    """把 ffprobe 的 '30000/1001' 之类的分数转成浮点。"""
    if not text:
        return None
    try:
        if "/" in str(text):
            num, den = str(text).split("/", 1)
            den = float(den)
            return float(num) / den if den else None
        return float(text)
    except (TypeError, ValueError):
        return None


def ffprobe_media(path) -> dict | None:
    """
    用 ffprobe 读出媒体硬指标。图片和音频同样适用。
    返回 None 表示 ffprobe 不可用或读取失败。
    """
    exe = ffprobe_bin()
    if not exe:
        return None
    cmd = [
        exe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        meta = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return None

    streams = meta.get("streams") or []
    fmt = meta.get("format") or {}
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    result: dict = {}
    if v:
        w, h = v.get("width"), v.get("height")
        if w and h:
            result["width"] = int(w)
            result["height"] = int(h)
            result["aspect_ratio"] = f"{int(w)}:{int(h)}"
        fps = _eval_ratio(v.get("r_frame_rate")) or _eval_ratio(v.get("avg_frame_rate"))
        if fps:
            result["fps"] = round(fps, 3)
        if v.get("codec_name"):
            result["video_codec"] = v["codec_name"]
        if v.get("pix_fmt"):
            result["pix_fmt"] = v["pix_fmt"]
        if v.get("pix_fmt") in ("rgba", "pal8", "ya8"):
            result["transparent_bg"] = True
    if a:
        result["has_audio"] = True
        if a.get("codec_name"):
            result["audio_codec"] = a["codec_name"]
        if a.get("sample_rate"):
            try:
                result["sample_rate"] = int(a["sample_rate"])
            except (TypeError, ValueError):
                pass
        if a.get("channels"):
            result["channels"] = int(a["channels"])
    elif v:
        result["has_audio"] = False

    duration = fmt.get("duration") or (v or {}).get("duration")
    if duration:
        try:
            d = float(duration)
            if d > 0:
                result["duration_sec"] = round(d, 3)
        except (TypeError, ValueError):
            pass

    bitrate = fmt.get("bit_rate")
    if bitrate:
        try:
            result["bitrate_kbps"] = round(float(bitrate) / 1000.0, 1)
        except (TypeError, ValueError):
            pass

    return result or None


def measure_loudness(path) -> dict | None:
    """
    用 ffmpeg loudnorm 测音频响度（EBU R128 / LUFS）。
    返回 {'input_i','input_lra','input_tp','input_thresh'} 或 None。
    """
    exe = ffmpeg_bin()
    if not exe:
        return None
    cmd = [
        exe, "-hide_banner", "-nostats", "-i", str(path),
        "-af", "loudnorm=print_format=json", "-f", "null", "-",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stderr or ""
    start, end = text.rfind("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    keep = ("input_i", "input_lra", "input_tp", "input_thresh")
    parsed = {}
    for key in keep:
        if key in data:
            try:
                parsed[key] = float(data[key])
            except (TypeError, ValueError):
                pass
    return parsed or None


def sample_video_frames(video_path, count: int = 8) -> list[Path]:
    """按时间均匀抽 count 帧，返回临时 PNG 路径列表（调用方负责清理目录）。

    抽帧失败时返回空列表，并清理临时目录、把 ffmpeg 的报错打出来 —— 不要静默失败，
    否则上层会把「抽帧没成功」误判成「画面全黑」，得出错误结论。
    """
    exe = ffmpeg_bin()
    if not exe:
        return []
    media = ffprobe_media(video_path) or {}
    duration = media.get("duration_sec")
    tmp = Path(tempfile.mkdtemp(prefix="plib_frames_"))
    if duration and duration > 0:
        rate = max(count / duration, 0.05)
    else:
        rate = 1.0
    cmd = [
        exe, "-y", "-v", "error", "-i", str(video_path),
        "-vf", f"fps={rate:.4f}",
        "-frames:v", str(count),
        str(tmp / "f_%03d.png"),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        eprint(f"[pipeline_lib] 抽帧失败：{video_path}（{type(exc).__name__}: {exc}）")
        return []
    frames = sorted(tmp.glob("f_*.png"))
    if not frames:
        shutil.rmtree(tmp, ignore_errors=True)
        eprint(f"[pipeline_lib] 抽帧失败：{video_path} → "
               f"{(out.stderr or '').strip()[:200] or '未产出任何帧'}")
    return frames


# --------------------------------------------------------------------------
# 2. 色彩度量
# --------------------------------------------------------------------------

def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_to_hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(int(round(float(c))) for c in rgb[:3]))


_SRGB_M = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
]) if np is not None else None


def _srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def pixels_to_lab(pixels):
    """(N,3) uint8 -> (N,3) Lab (D65)。"""
    c = np.asarray(pixels, dtype=np.float64) / 255.0
    lin = _srgb_to_linear(c)
    xyz = lin @ _SRGB_M.T
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(np.maximum(xyz, 0.0)), (7.787 * xyz) + 16.0 / 116.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def delta_e76(lab_a, lab_b) -> float:
    """CIE76 色差。经验刻度：<2 肉眼难辨，~12 同色系，>25 已属不同颜色。"""
    a = np.asarray(lab_a, dtype=np.float64)
    b = np.asarray(lab_b, dtype=np.float64)
    return float(np.sqrt(((a - b) ** 2).sum()))


def estimate_cct(pixels) -> float | None:
    """
    灰世界白点估计 + McCamy 公式换算相关色温（K）。

    注意：McCamy 只在接近黑体轨迹时可靠（中性色、灰白、灯光）。画面严重偏色时
    该值只反映「整体偏暖还是偏冷」的方向，不能当绝对色温用，因此配方卡里同时
    保留 warmth_index 作为稳健代理指标。
    """
    flat = np.asarray(pixels, dtype=np.float64) / 255.0
    if flat.size == 0:
        return None
    lin = _srgb_to_linear(flat).mean(axis=0)
    total = lin.sum()
    if total <= 1e-9:
        return None
    xyz = _SRGB_M @ (lin / total)
    s = xyz.sum()
    if s <= 1e-9:
        return None
    x, y = xyz[0] / s, xyz[1] / s
    if abs(0.1858 - y) < 1e-9:
        return None
    n = (x - 0.3320) / (0.1858 - y)
    cct = 449.0 * n ** 3 + 3525.0 * n ** 2 + 6823.3 * n + 5520.33
    if not np.isfinite(cct) or cct <= 0:
        return None
    return float(cct)


def frame_stats(pixels) -> dict:
    """单帧/单素材的调色类指标。

    指标定义（必须与配方卡里的口径一致，否则容差没有意义）：
      brightness_avg  平均亮度 = mean(0.2126R+0.7152G+0.0722B)，输入为 0-1 的 sRGB 编码值
      contrast_rms    对比度   = 上述亮度的标准差（RMS 反差）
      saturation_avg  平均彩度 = mean(max(R,G,B)-min(R,G,B))，即「彩度/255」口径，
                      不是 HSV 的 S（深色在 HSV 里会被算成高饱和，无法与配方卡对齐）
      saturation_hsv  保留 HSV 的 S 均值，仅供人工参考，不参与打分
      warmth_index    暖度     = mean(R)-mean(B)，负=偏冷，正=偏暖；比色温稳健，不依赖白点假设
      cct_k           相关色温 = 灰世界白点 + McCamy 公式估计，仅在接近中性色时可靠
    """
    flat = np.asarray(pixels, dtype=np.float64) / 255.0
    if flat.size == 0:
        return {}
    r, g, b = flat[:, 0], flat[:, 1], flat[:, 2]
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    mx = flat.max(axis=1)
    mn = flat.min(axis=1)
    chroma = mx - mn
    sat_hsv = np.where(mx > 1e-9, chroma / np.maximum(mx, 1e-9), 0.0)
    brightness = float(luma.mean())
    cct = estimate_cct(pixels)
    return {
        "brightness_avg": round(brightness, 4),
        "contrast_rms": round(float(luma.std()), 4),
        "saturation_avg": round(float(chroma.mean()), 4),
        "saturation_hsv_avg": round(float(sat_hsv.mean()), 4),
        "warmth_index": round(float(r.mean() - b.mean()), 4),
        "cct_k": (round(cct, 1) if cct else None),
        "is_black_frame": bool(brightness < 0.04),
        "pixel_count": int(len(flat)),
    }


def dominant_palette(pixels, k: int = 5, iters: int = 14, seed: int = 7) -> list[dict]:
    """k-means 主色板，按占比降序。"""
    flat = np.asarray(pixels, dtype=np.float64)
    if flat.size == 0:
        return []
    k = max(1, min(k, len(flat)))
    rng = np.random.default_rng(seed)
    centers = flat[rng.choice(len(flat), k, replace=False)].copy()
    labels = np.zeros(len(flat), dtype=int)
    for _ in range(iters):
        d = ((flat[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels = d.argmin(axis=1)
        moved = False
        for j in range(k):
            sel = labels == j
            if sel.any():
                new = flat[sel].mean(axis=0)
                if not np.allclose(new, centers[j]):
                    moved = True
                centers[j] = new
        if not moved:
            break
    counts = np.bincount(labels, minlength=k).astype(float)
    total = counts.sum() or 1.0
    order = np.argsort(-counts)
    return [
        {"hex": rgb_to_hex(centers[j]), "share": round(float(counts[j] / total), 4)}
        for j in order
        if counts[j] > 0
    ]


def palette_hit_rate(pixels, palette_hex: list[str], tol_delta_e: float = 12.0) -> float:
    """画面像素落在配方色板容差内的比例（0-1）。"""
    if not palette_hex:
        return 0.0
    lab = pixels_to_lab(pixels)
    refs = np.array([pixels_to_lab(np.array([hex_to_rgb(h)], dtype=np.uint8))[0] for h in palette_hex])
    d = np.sqrt(((lab[:, None, :] - refs[None, :, :]) ** 2).sum(axis=2))
    return round(float((d.min(axis=1) <= tol_delta_e).mean()), 4)


def color_hit_rate(pixels, hex_color: str, tol_delta_e: float = 8.0) -> dict:
    """某个指定颜色（如地图路线描边色）在画面中的命中率与平均色差。"""
    lab = pixels_to_lab(pixels)
    ref = pixels_to_lab(np.array([hex_to_rgb(hex_color)], dtype=np.uint8))[0]
    d = np.sqrt(((lab - ref) ** 2).sum(axis=1))
    hit = d <= tol_delta_e
    return {
        "target": hex_color,
        "hit_rate": round(float(hit.mean()), 4),
        "min_delta_e": round(float(d.min()), 2),
        "avg_delta_e_of_hits": (round(float(d[hit].mean()), 2) if hit.any() else None),
    }


def _hue_degrees(pixels):
    c = np.asarray(pixels, dtype=np.float64) / 255.0
    r, g, b = c[:, 0], c[:, 1], c[:, 2]
    mx = c.max(axis=1)
    mn = c.min(axis=1)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 1e-9
    m = nz & (mx == r)
    h[m] = ((g - b)[m] / d[m]) % 6.0
    m = nz & (mx == g)
    h[m] = ((b - r)[m] / d[m]) + 2.0
    m = nz & (mx == b)
    h[m] = ((r - g)[m] / d[m]) + 4.0
    return (h * 60.0) % 360.0


def hue_histogram(pixels, bins: int = 24) -> "np.ndarray":
    """饱和度加权的色相直方图，已归一化。灰阶像素权重趋近 0。"""
    c = np.asarray(pixels, dtype=np.float64) / 255.0
    mx = c.max(axis=1)
    mn = c.min(axis=1)
    sat = np.where(mx > 1e-9, (mx - mn) / np.maximum(mx, 1e-9), 0.0)
    hist, _ = np.histogram(_hue_degrees(pixels), bins=bins, range=(0.0, 360.0), weights=sat)
    total = hist.sum()
    return hist / total if total > 0 else hist


def chi2_distance(hist_a, hist_b) -> float:
    """归一化直方图之间的对称 χ² 距离，0=完全一致，1=完全不相交。"""
    a = np.asarray(hist_a, dtype=np.float64)
    b = np.asarray(hist_b, dtype=np.float64)
    denom = np.maximum(a + b, 1e-9)
    return round(float(0.5 * np.sum(((a - b) ** 2) / denom)), 4)


# --------------------------------------------------------------------------
# 3. 像素装载
# --------------------------------------------------------------------------

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def load_pixels(path, sample: int = 60000, seed: int = 7):
    """
    读取图片为抽样后的像素数组 (N,3) uint8。
    带 alpha 的素材（如透明底图标）会先剔除透明像素，只分析主体颜色。
    """
    if Image is None:
        raise RuntimeError("缺少 Pillow，请先执行 pip install -r requirements.txt")
    if np is None:
        raise RuntimeError("缺少 numpy，请先执行 pip install -r requirements.txt")

    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        arr = np.asarray(img)
        rgb = arr[..., :3].reshape(-1, 3)
        alpha = arr[..., 3].reshape(-1)
        rgb = rgb[alpha > 16]
    else:
        rgb = np.asarray(img.convert("RGB")).reshape(-1, 3)
    if len(rgb) == 0:
        rgb = np.asarray(img.convert("RGB")).reshape(-1, 3)
    if len(rgb) > sample:
        rng = np.random.default_rng(seed)
        rgb = rgb[rng.choice(len(rgb), sample, replace=False)]
    return np.ascontiguousarray(rgb, dtype=np.uint8)


def iter_assets(root: Path, include_video: bool = True):
    """遍历目录下所有可分析的图片/视频素材。"""
    exts = set(IMAGE_EXT) | (set(VIDEO_EXT) if include_video else set())
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in exts:
            yield p


def load_json(path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path, data) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def eprint(*args) -> None:
    print(*args, file=sys.stderr)
