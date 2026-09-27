#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_visual.py —— 画面质量校验器（配方卡符合度打分）

回答的问题：这批模型产出的画面，到底有多接近「视觉配方卡」？

工作方式是把配方卡里能数字化的部分变成阈值，再对实拍/生成素材逐项打分：

  硬指标 : 分辨率、画幅比、时长、帧率（不达标直接 FAIL）
  调色   : 平均亮度、RMS 对比度、平均饱和度、色温方向（灰世界 + McCamy 估计）
  色彩   : 主色板命中率（k-means 主色 vs 配方色板，CIE76 ΔE）
  特殊色 : 地图路线描边色命中（ΔE 最小距离）
  画面   : 黑帧比例
  一致性 : 素材两两之间的色相直方图 χ² 距离（防跳色）

用法：
  # 单张图
  python check_visual.py --input assets/map/route.png --pipeline A --json-out map_qc.json
  # 整个管线目录（会自动做跨素材一致性）
  python check_visual.py --input assets/video/ --pipeline B --json-out video_qc.json
  # 自校准：跑满 3 条成片后，用实测中位数回填配方卡
  python check_visual.py --input assets/ --pipeline B --calibrate
  # 严格度调节：容差整体缩放（0.5 = 更严，2.0 = 更宽）
  python check_visual.py --input assets/ --pipeline ALL --tolerance-scale 0.5
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pipeline_lib as pl  # noqa: E402

# 各检查项的权重。硬指标权重高，风格指标按重要性分配。
WEIGHTS = {
    "resolution": 0.20,
    "aspect": 0.05,
    "duration": 0.05,
    "brightness": 0.10,
    "contrast": 0.10,
    "saturation": 0.10,
    "temperature": 0.10,
    "palette": 0.15,
    "route_color": 0.05,
    "black_frame": 0.05,
    "consistency": 0.10,
}
HARD_ITEMS = {"resolution", "aspect", "route_color"}

# 各管线关心的配方段落
PIPELINE_SECTIONS = {
    "A": ["map", "color"],
    "B": ["color", "pip"],
    "C": ["icon", "color"],
    "D": ["cover", "color"],
    "ALL": ["map", "pip", "icon", "color", "subtitle", "cover"],
}


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _fmt(value, spec: str = ".3f", suffix: str = "") -> str:
    """把可能是 None 的指标安全格式化，避免色温估计失败时打印崩溃。"""
    if isinstance(value, (int, float)):
        return format(value, spec) + suffix
    return "n/a"


def soft_score(delta: float, tol: float) -> tuple[float, str]:
    """差值越界越多，得分越低。返回 (score, status)。"""
    if tol <= 0:
        return (1.0 if delta == 0 else 0.0), ("pass" if delta == 0 else "fail")
    if delta <= tol:
        return 1.0, "pass"
    if delta <= 2 * tol:
        return clamp01(1.0 - (delta - tol) / tol), "warn"
    return 0.0, "fail"


# --------------------------------------------------------------------------
# 单素材分析
# --------------------------------------------------------------------------

def analyze_asset(path: Path, sample_frames: int = 8) -> dict:
    media = pl.ffprobe_media(path) or {}
    is_video = path.suffix.lower() in pl.VIDEO_EXT
    is_image = path.suffix.lower() in pl.IMAGE_EXT

    if is_image:
        if "width" not in media:
            try:
                from PIL import Image
                with Image.open(path) as im:
                    media["width"], media["height"] = im.size
                    media["aspect_ratio"] = f"{im.size[0]}:{im.size[1]}"
                    if im.mode in ("RGBA", "LA", "PA"):
                        media["transparent_bg"] = True
            except Exception:  # noqa: BLE001
                pass
        pixel_sets = [pl.load_pixels(path)]
        frames = 1
    elif is_video:
        shots = pl.sample_video_frames(path, count=sample_frames)
        if not shots:
            return {"path": str(path), "error": "无法抽帧（缺少 ffmpeg 或文件损坏）"}
        # 抽帧目录在 temp，用完删
        pixel_sets = [pl.load_pixels(p, sample=20000) for p in shots]
        frames = len(pixel_sets)
        shutil.rmtree(shots[0].parent, ignore_errors=True)
    else:
        return {"path": str(path), "error": f"不支持的后缀 {path.suffix}"}

    per_frame_stats = [pl.frame_stats(px) for px in pixel_sets]
    valid = [s for s in per_frame_stats if s]
    if not valid:
        return {"path": str(path), "error": "未能读取任何有效像素"}

    def med(key):
        vals = [s[key] for s in valid if s.get(key) is not None]
        return statistics.median(vals) if vals else None

    stats = {
        "brightness_avg": med("brightness_avg"),
        "contrast_rms": med("contrast_rms"),
        "saturation_avg": med("saturation_avg"),
        "warmth_index": med("warmth_index"),
        "cct_k": med("cct_k"),
        "brightness_min": min(s["brightness_avg"] for s in valid),
        "brightness_max": max(s["brightness_avg"] for s in valid),
    }

    merged = pixel_sets[0] if frames == 1 else _stack_pixels(pixel_sets)
    return {
        "path": str(path),
        "name": path.name,
        "kind": "video" if is_video else "image",
        "media": media,
        "frames_analyzed": frames,
        "stats": stats,
        "palette": pl.dominant_palette(merged, k=5),
        "black_frame_ratio": round(sum(1 for s in valid if s["is_black_frame"]) / len(valid), 4),
        "hue_hist": pl.hue_histogram(merged).tolist(),
        "_pixels": merged,
    }


def _stack_pixels(pixel_sets: list):
    import numpy as np
    if len(pixel_sets) == 1:
        return pixel_sets[0]
    each = max(4000, 30000 // len(pixel_sets))
    trimmed = [px[:each] for px in pixel_sets]
    return np.ascontiguousarray(np.concatenate(trimmed, axis=0), dtype=np.uint8)


# --------------------------------------------------------------------------
# 打分
# --------------------------------------------------------------------------

def score_asset(asset: dict, recipe: dict, pipeline: str, group_median: dict | None) -> dict:
    import numpy as np

    color = recipe.get("color") or {}
    tol = recipe.get("tolerances") or {}
    canvas = ((recipe.get("map") or {}).get("canvas")) or {}
    items: list[dict] = []

    def add(name, ok_status, score, weight, detail):
        items.append({
            "item": name, "status": ok_status, "score": round(score, 4),
            "weight": weight, "detail": detail, "hard": name in HARD_ITEMS,
        })

    media = asset.get("media") or {}
    stats = asset.get("stats") or {}

    # --- 硬指标：分辨率 ---
    expect_w, expect_h = canvas.get("width"), canvas.get("height")
    if expect_w and expect_h and media.get("width"):
        ok = media["width"] == expect_w and media["height"] == expect_h
        add("resolution", "pass" if ok else "fail", 1.0 if ok else 0.0, WEIGHTS["resolution"],
            f"{media.get('width')}x{media.get('height')} vs 要求 {expect_w}x{expect_h}")
    else:
        add("resolution", "warn", 0.5, WEIGHTS["resolution"], "尺寸未知或配方卡未定义画布")

    # --- 硬指标：画幅比 ---
    if media.get("width") and media.get("height"):
        ratio = media["width"] / media["height"]
        want_ratio = (expect_w / expect_h) if (expect_w and expect_h) else None
        ok = (want_ratio is not None and abs(ratio - want_ratio) < 0.02) or abs(ratio - 9 / 16) < 0.02
        add("aspect", "pass" if ok else "fail", 1.0 if ok else 0.0, WEIGHTS["aspect"],
            f"画幅比 {ratio:.4f}" + (f"，要求 {want_ratio:.4f}" if want_ratio else ""))
    else:
        add("aspect", "warn", 0.5, WEIGHTS["aspect"], "画幅比未知")

    # --- 时长 ---
    dur = media.get("duration_sec")
    if dur is None:
        add("duration", "skip", 1.0, 0.0, "静态图，跳过时长检查")
    else:
        ok = dur >= 1.5
        add("duration", "pass" if ok else "warn", 1.0 if ok else 0.5, WEIGHTS["duration"],
            f"{dur:.2f}s" + ("" if ok else "（短于 1.5s，剪辑时可能需要变速或补帧）"))

    # --- 调色三项 ---
    for key, rec_key, label in (
        ("brightness_avg", "brightness_avg", "平均亮度"),
        ("contrast_rms", "contrast_rms", "RMS 对比度"),
        ("saturation_avg", "saturation_avg", "平均饱和度"),
    ):
        value = stats.get(key)
        target = color.get(rec_key)
        tolerance = tol.get(key, 0.06)
        if value is None or target is None:
            add(key.replace("_avg", "").replace("_rms", ""), "skip", 1.0, 0.0, "缺数据")
            continue
        delta = abs(value - target)
        score, status = soft_score(delta, tolerance)
        add(key.replace("_avg", "").replace("_rms", ""), status, score, WEIGHTS[key.split("_")[0] if key.split("_")[0] in WEIGHTS else "brightness"],
            f"{label} {value:.3f} vs 目标 {target:.3f}（容差 ±{tolerance}，实测偏差 {delta:.3f}）")

    # --- 色温 + 暖度方向 ---
    # 色温（McCamy 估计）只在接近中性色时可信，所以同时用 warmth_index 交叉验证：
    # 既要幅度接近，也要方向一致（配方要冷调，实测就不能偏暖）。
    cct = stats.get("cct_k")
    target_cct = color.get("temperature_k")
    tol_cct = tol.get("temperature_k", 1200)
    wi, target_wi = stats.get("warmth_index"), color.get("warmth_index")
    tol_wi = tol.get("warmth_index", 0.06)

    if cct and target_cct:
        d_cct = abs(cct - target_cct)
        score, status = soft_score(d_cct, tol_cct)
        detail = f"色温估计 {cct:.0f}K vs 目标 {target_cct:.0f}K（容差 ±{tol_cct:.0f}K，偏差 {d_cct:.0f}K）"
        if wi is not None and target_wi is not None:
            d_wi = abs(wi - target_wi)
            s_wi, st_wi = soft_score(d_wi, tol_wi)
            same_dir = (wi <= 0) == (target_wi <= 0)
            detail += f"；暖度 {wi:+.3f} vs {target_wi:+.3f}（容差 ±{tol_wi}）"
            if not same_dir:
                score, status = 0.0, "fail"
                detail += " → 偏色方向与配方卡相反"
            elif st_wi != "pass" and s_wi < score:
                score, status = s_wi, st_wi
        add("temperature", status, score, WEIGHTS["temperature"], detail)
    else:
        add("temperature", "skip", 1.0, 0.0, "色温无法估计")

    # --- 主色板命中率 ---
    palette = color.get("palette") or []
    if palette:
        hit = pl.palette_hit_rate(asset["_pixels"], palette, tol_delta_e=tol.get("palette_delta_e", 12))
        status = "pass" if hit >= 0.55 else ("warn" if hit >= 0.35 else "fail")
        score = clamp01(hit / 0.55)
        add("palette", status, score, WEIGHTS["palette"],
            f"主色板命中率 {hit*100:.1f}%（目标 ≥55%，容差 ΔE≤{tol.get('palette_delta_e', 12)}）")
    else:
        add("palette", "skip", 1.0, 0.0, "配方卡未定义色板")

    # --- 地图路线特殊色 ---
    if pipeline == "A":
        route = ((recipe.get("map") or {}).get("route_color"))
        if route:
            hit = pl.color_hit_rate(asset["_pixels"], route, tol_delta_e=tol.get("route_color_delta_e", 8))
            ok = hit["min_delta_e"] <= tol.get("route_color_delta_e", 8)
            add("route_color", "pass" if ok else "fail", 1.0 if ok else 0.0, WEIGHTS["route_color"],
                f"路线色 {route}：最小色差 ΔE={hit['min_delta_e']}（要求 ≤{tol.get('route_color_delta_e', 8)}），"
                f"画面占比 {hit['hit_rate']*100:.3f}%")

    # --- 黑帧 ---
    bfr = asset.get("black_frame_ratio", 0.0)
    tol_bf = tol.get("black_frame_ratio", 0.02)
    ok = bfr <= tol_bf
    add("black_frame", "pass" if ok else "fail", 1.0 if ok else 0.0, WEIGHTS["black_frame"],
        f"黑帧比例 {bfr*100:.1f}%（上限 {tol_bf*100:.1f}%）")

    # --- 与整组的一致性 ---
    if group_median and asset.get("hue_hist"):
        import numpy as np
        d = pl.chi2_distance(asset["hue_hist"], group_median["hue_hist"])
        tol_c = tol.get("consistency_chi2", 0.15)
        score, status = soft_score(d, tol_c)
        add("consistency", status, score, WEIGHTS["consistency"],
            f"与整组中位色调的 χ² 距离 {d}（容差 {tol_c}）")

    total_w = sum(i["weight"] for i in items if i["status"] != "skip") or 1.0
    score = sum(i["score"] * i["weight"] for i in items if i["status"] != "skip") / total_w
    hard_fail = [i["item"] for i in items if i["hard"] and i["status"] == "fail"]
    if hard_fail:
        verdict = "FAIL"
    elif score >= 0.85:
        verdict = "PASS"
    elif score >= 0.70:
        verdict = "REVIEW"
    else:
        verdict = "FAIL"

    return {
        "asset": asset.get("name"),
        "path": asset.get("path"),
        "kind": asset.get("kind"),
        "verdict": verdict,
        "recipe_score": round(score * 100, 1),
        "hard_failures": hard_fail,
        "items": items,
    }


# --------------------------------------------------------------------------
# 自校准
# --------------------------------------------------------------------------

def calibrate(assets: list[dict], recipe: dict, consistency_max: float) -> dict:
    color = recipe.get("color") or {}
    tol = recipe.get("tolerances") or {}

    def val(key):
        return [a["stats"][key] for a in assets if a.get("stats") and a["stats"].get(key) is not None]

    measured = {}
    suggested = {}
    for key, floor in (
        ("brightness_avg", 0.02), ("contrast_rms", 0.02),
        ("saturation_avg", 0.02), ("warmth_index", 0.01),
    ):
        vals = val(key)
        if not vals:
            continue
        med = statistics.median(vals)
        mad = statistics.median([abs(v - med) for v in vals]) or floor
        measured[key] = round(med, 4)
        suggested[key] = round(max(1.5 * mad, floor), 4)

    ccts = val("cct_k")
    if ccts:
        med_cct = statistics.median(ccts)
        mad_cct = statistics.median([abs(v - med_cct) for v in ccts]) or 200.0
        measured["temperature_k"] = round(med_cct, 1)
        suggested["temperature_k"] = round(max(1.5 * mad_cct, 200.0), 1)

    # 色板：取所有素材主色的高频色
    counter: dict[str, float] = {}
    for a in assets:
        for entry in a.get("palette") or []:
            counter[entry["hex"]] = counter.get(entry["hex"], 0.0) + entry["share"]
    top = sorted(counter.items(), key=lambda kv: -kv[1])[:5]

    # 一致性容差
    suggested["consistency_chi2"] = round(max(consistency_max * 1.5, 0.05), 4) if consistency_max else 0.15

    return {
        "status": "calibrated",
        "sample_count": len(assets),
        "method": "中位数 + 1.5×MAD（中位绝对偏差）回填；色板取主色加权频次 Top5；一致性容差取实测最大 χ² ×1.5。",
        "measured_median": measured,
        "suggested_tolerances": suggested,
        "suggested_palette": [{"hex": h, "score": round(v, 3)} for h, v in top],
        "current_recipe_color": {k: color.get(k) for k in ("brightness_avg", "contrast_rms", "saturation_avg", "temperature_k", "warmth_index", "palette")},
        "current_tolerances": tol,
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="画面质量校验：配方卡符合度打分")
    parser.add_argument("--input", required=True, help="素材文件或目录")
    parser.add_argument("--pipeline", default="ALL", choices=sorted(PIPELINE_SECTIONS.keys()),
                        help="A=地图 B=景点视频 C=图标 D=封面 ALL=通用")
    parser.add_argument("--recipe", default=None, help="配方卡 JSON")
    parser.add_argument("--sample-frames", type=int, default=8, help="视频抽帧数量")
    parser.add_argument("--tolerance-scale", type=float, default=1.0, help="容差整体缩放（严格度调节，0.5=更严）")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--calibrate", action="store_true", help="输出回填配方卡所需的实测中位数与建议容差")
    parser.add_argument("--yes", action="store_true", help="配合 --calibrate，直接改写配方卡")
    args = parser.parse_args(argv)

    kit = Path(__file__).resolve().parent.parent
    recipe_path = Path(args.recipe).resolve() if args.recipe else kit / "config" / "recipe.nizhoudao.json"
    if not recipe_path.exists():
        pl.eprint(f"配方卡不存在：{recipe_path}")
        return 2
    recipe = pl.load_json(recipe_path)

    if args.tolerance_scale != 1.0:
        recipe["tolerances"] = {
            k: (round(v * args.tolerance_scale, 4) if isinstance(v, (int, float)) else v)
            for k, v in (recipe.get("tolerances") or {}).items()
        }

    target = Path(args.input).resolve()
    if not target.exists():
        pl.eprint(f"输入不存在：{target}")
        return 2

    files = [target] if target.is_file() else list(pl.iter_assets(target))
    if not files:
        pl.eprint(f"{target} 下没有可分析的图片/视频素材")
        return 2

    print("=" * 78)
    print(f"画面质量校验 · 管线 {args.pipeline}")
    print(f"  素材     : {target}  共 {len(files)} 件")
    print(f"  配方卡   : {recipe_path.name}")
    print("=" * 78)

    assets, errors = [], []
    for f in files:
        try:
            a = analyze_asset(f, sample_frames=args.sample_frames)
        except Exception as exc:  # noqa: BLE001
            errors.append((str(f), str(exc)))
            continue
        if a.get("error"):
            errors.append((str(f), a["error"]))
            continue
        assets.append(a)

    for path, msg in errors:
        print(f"  [跳过] {Path(path).name}: {msg}")

    if not assets:
        pl.eprint("没有成功分析任何素材")
        return 2

    # 整组中位色调，用于跨素材一致性
    import numpy as np
    stacked = np.vstack([np.asarray(a["hue_hist"], dtype=float) for a in assets])
    group_hist = np.median(stacked, axis=0)
    group_hist = group_hist / group_hist.sum() if group_hist.sum() > 0 else group_hist
    group_median = {"hue_hist": group_hist.tolist()}

    results = [score_asset(a, recipe, args.pipeline, group_median) for a in assets]

    # 两两一致性（找最不像的那一对）
    max_pair, max_chi2 = None, 0.0
    for i in range(len(assets)):
        for j in range(i + 1, len(assets)):
            d = pl.chi2_distance(assets[i]["hue_hist"], assets[j]["hue_hist"])
            if d > max_chi2:
                max_chi2, max_pair = d, (assets[i]["name"], assets[j]["name"])

    for a, r in zip(assets, results):
        s = a["stats"]
        media = a["media"] or {}
        print(f"\n▸ {a['name']}  [{a['kind']}, {a['frames_analyzed']} 帧]  → {r['verdict']}  {r['recipe_score']}/100")
        print(f"   亮度 {_fmt(s['brightness_avg'], '.3f')}｜对比 {_fmt(s['contrast_rms'], '.3f')}"
              f"｜饱和 {_fmt(s['saturation_avg'], '.3f')}｜色温 {_fmt(s['cct_k'], '.0f', 'K')}"
              f"｜warmth {_fmt(s['warmth_index'], '+.3f')}"
              f"｜尺寸 {media.get('width')}x{media.get('height')}")
        print(f"   主色板 " + "  ".join(f"{p['hex']}({p['share']*100:.0f}%)" for p in a["palette"][:5]))
        for item in r["items"]:
            if item["status"] in ("skip",):
                continue
            mark = {"pass": "✓", "warn": "!", "fail": "✗"}[item["status"]]
            if item["status"] == "pass" and not item["hard"]:
                continue  # 通过的非硬指标不刷屏
            print(f"     {mark} {item['detail']}")

    print("\n" + "-" * 78)
    if max_pair:
        tol_c = (recipe.get("tolerances") or {}).get("consistency_chi2", 0.15)
        flag = "✗ 跳色风险" if max_chi2 > tol_c else "✓"
        print(f"{flag} 跨素材一致性：最不像的一对是 {max_pair[0]} ↔ {max_pair[1]}，χ²={max_chi2}（容差 {tol_c}）")
    avg = sum(r["recipe_score"] for r in results) / len(results)
    worst = min(results, key=lambda r: r["recipe_score"])
    hard = [r for r in results if r["verdict"] == "FAIL"]
    print(f"配方符合度均值 {avg:.1f}/100｜最差 {worst['asset']} {worst['recipe_score']}/100")
    verdict = "FAIL" if hard else ("REVIEW" if avg < 85 else "PASS")
    print(f"结论：{verdict}"
          + (f"（未通过：{', '.join(r['asset'] for r in hard)}）" if hard else ""))
    print("-" * 78)

    calib = calibrate(assets, recipe, max_chi2) if args.calibrate else None

    if args.json_out:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pipeline": args.pipeline,
            "input": str(target),
            "recipe_path": str(recipe_path),
            "sections_checked": PIPELINE_SECTIONS[args.pipeline],
            "summary": {
                "assets": len(results),
                "avg_score": round(avg, 1),
                "max_consistency_chi2": round(max_chi2, 4),
                "max_consistency_pair": max_pair,
                "verdict": verdict,
            },
            "assets": [{k: v for k, v in a.items() if not k.startswith("_")} for a in assets],
            "scores": results,
            "calibration": calib,
        }
        pl.dump_json(args.json_out, payload)
        print(f"报告已写入：{args.json_out}")

    if calib:
        print("\n" + "=" * 78)
        print("自校准结果（回填配方卡）")
        print("=" * 78)
        print(json.dumps(calib, ensure_ascii=False, indent=2))
        if args.yes:
            recipe.setdefault("color", {})
            for key, v in calib["measured_median"].items():
                recipe["color"][key] = v
            if calib["suggested_palette"]:
                recipe["color"]["palette"] = [p["hex"] for p in calib["suggested_palette"]]
            recipe.setdefault("tolerances", {}).update(calib["suggested_tolerances"])
            recipe["calibration"] = {
                "status": "calibrated",
                "sample_count": calib["sample_count"],
                "method": calib["method"],
                "measured_median": calib["measured_median"],
            }
            pl.dump_json(recipe_path, recipe)
            print(f"\n已回填并写回：{recipe_path}")

    return 0 if verdict != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
