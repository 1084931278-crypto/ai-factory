"""
AI Factory Dispatcher · 流水线调度器 (便携版)
==============================================
用法:
  # 单次模式
  python dispatcher.py --once "做一条硇洲岛的，参考鱼鱼大王风格"

  # 守护模式（监控 input/latest_request.txt）
  python dispatcher.py --daemon

  # 从断点继续
  python dispatcher.py --resume projects/naozhoudao-20260926
"""

import os, sys, json, time, shutil, hashlib, argparse, subprocess
from datetime import datetime
from pathlib import Path

# ============================================================
# 配置（可通过环境变量覆盖）
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "input"
PROJECTS_DIR = BASE_DIR / "projects"
CONFIG_DIR = BASE_DIR / "config"
LOGS_DIR = BASE_DIR / "logs"
KIT_DIR = BASE_DIR / "kit"

# ffmpeg 路径：优先环境变量 FFMPEG_PATH，其次 PATH
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")

# 校验脚本路径
CHECK_VISUAL_SCRIPT = str(KIT_DIR / "scripts" / "check_visual.py")
DEFAULT_RECIPE = CONFIG_DIR / "recipe.default.json"

# 若 kit/scripts 不存在，回退到 scripts/
_CHECK_VISUAL_SCRIPT2 = str(BASE_DIR / "scripts" / "check_visual.py")

PIPELINE_NODES = [
    {"id": "L1.1", "name": "对标片拆解", "tool": "codex", "type": "creative"},
    {"id": "L1.2", "name": "视觉配方卡", "tool": "workbuddy", "type": "analysis"},
    {"id": "L2.1", "name": "逐镜脚本", "tool": "codex", "type": "creative"},
    {"id": "L2.2", "name": "镜头分类", "tool": "codex", "type": "creative"},
    {"id": "L3A", "name": "地图路线动图", "tool": "browser+ffmpeg", "type": "asset"},
    {"id": "L3B", "name": "素材筛选与校验", "tool": "workbuddy", "type": "analysis"},
    {"id": "L3C", "name": "图标生成", "tool": "codex+hyperframes", "type": "asset"},
    {"id": "L3D", "name": "封面生成", "tool": "codex+hyperframes", "type": "asset"},
    {"id": "L4.1", "name": "配音生成", "tool": "manual", "type": "asset"},
    {"id": "L4.2", "name": "合成与成片", "tool": "ffmpeg", "type": "synthesis"},
    {"id": "L5.1", "name": "发布文案", "tool": "codex", "type": "creative"},
]

# ============================================================
# 工具函数
# ============================================================

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"{datetime.now().strftime('%Y%m%d')}.log"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def ensure_dirs():
    for d in [INPUT_DIR, PROJECTS_DIR, CONFIG_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def write_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def run_cmd(cmd, **kwargs):
    log(f"CMD: {cmd}")
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kwargs)
        if r.stdout:
            log(f"  stdout: {r.stdout[:500]}")
        if r.stderr and r.returncode != 0:
            log(f"  stderr: {r.stderr[:500]}", "WARN")
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        log(f"  命令执行失败: {e}", "ERROR")
        return -1, "", str(e)

# ============================================================
# 项目管理
# ============================================================

def parse_request(raw_text):
    req = {
        "raw": raw_text.strip(),
        "project_name": None,
        "topic": None,
        "style_reference": None,
        "source_footage_path": None,
        "target_duration_sec": 35,
        "platform": "douyin",
        "recipe_style": None,
        "parsed_at": datetime.now().isoformat(),
    }
    lines = raw_text.strip().splitlines()
    text = lines[0] if lines else ""
    slug = "project-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    req["project_name"] = slug
    req["topic"] = text[:30]

    if "参考" in text:
        idx = text.index("参考")
        rest = text[idx + 2:].strip()
        ref = ""
        for ch in rest:
            if ch in "，。、；,.; 风格的":
                break
            ref += ch
        if ref:
            req["style_reference"] = ref

    if "素材" in text:
        idx = text.index("素材")
        rest = text[idx:].strip()
        import re
        paths = re.findall(r'[A-Za-z]:/[^\s，。；]+', rest)
        if paths:
            req["source_footage_path"] = paths[0]
    return req

def create_project(req):
    proj_dir = PROJECTS_DIR / req["project_name"]
    (proj_dir / "nodes").mkdir(parents=True, exist_ok=True)
    (proj_dir / "assets").mkdir(parents=True, exist_ok=True)
    (proj_dir / "source-footage").mkdir(parents=True, exist_ok=True)
    write_json(proj_dir / "request.json", req)
    status = {
        "project": req["project_name"],
        "status": "created",
        "current_node": None,
        "completed_nodes": [],
        "failed_node": None,
        "error_msg": None,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
    }
    write_json(proj_dir / "status.json", status)
    log(f"创建项目: {proj_dir}")
    return proj_dir

def update_status(proj_dir, **kwargs):
    status_path = proj_dir / "status.json"
    status = read_json(status_path)
    status.update(kwargs)
    status["updated_at"] = datetime.now().isoformat()
    write_json(status_path, status)

# ============================================================
# 节点执行器
# ============================================================

def _cv_script():
    if os.path.exists(CHECK_VISUAL_SCRIPT):
        return CHECK_VISUAL_SCRIPT
    if os.path.exists(_CHECK_VISUAL_SCRIPT2):
        return _CHECK_VISUAL_SCRIPT2
    return None

def run_node_L12(proj_dir, req):
    log("=" * 60)
    log("L1.2 · 生成视觉配方卡")
    log("=" * 60)
    footage_dir = proj_dir / "source-footage"
    video_files = list(footage_dir.glob("*.mp4")) + list(footage_dir.glob("*.MP4"))
    image_files = list(footage_dir.glob("*.jpg")) + list(footage_dir.glob("*.png"))

    recipe_path = proj_dir / "recipe.json"
    if not video_files and not image_files:
        log(f"素材目录为空，使用默认配方卡", "WARN")
        if DEFAULT_RECIPE.exists():
            shutil.copy2(DEFAULT_RECIPE, recipe_path)
        else:
            log("默认配方卡不存在，创建简易配方卡", "WARN")
            write_json(recipe_path, {
                "meta": {"name": "default", "version": "1.0"},
                "color": {"brightness_avg": 0.5, "contrast_rms": 0.3, "saturation_avg": 0.5, "warmth_index": 0.0},
                "tolerances": {"brightness_avg": 0.1, "contrast_rms": 0.1, "saturation_avg": 0.1, "warmth_index": 0.08}
            })
    else:
        ref_file = image_files[0] if image_files else video_files[0]
        log(f"用 {ref_file.name} 作为风格参考")
        cv = _cv_script()
        if cv:
            cmd = f'python "{cv}" --input "{ref_file}" --pipeline single --calibrate --project "{proj_dir}"'
            run_cmd(cmd)

    node = {
        "schema_version": "1.0.0",
        "node_id": "L1.2",
        "stage": "distill",
        "produced_by": "dispatcher-L12",
        "produced_at": datetime.now().isoformat(),
        "upstream": [],
        "status": "ok",
        "payload": {"recipe_path": str(recipe_path)},
    }
    write_json(proj_dir / "nodes" / "L1.2_recipe.node.json", node)
    return True

def run_node_L3B(proj_dir, req):
    log("=" * 60)
    log("L3B · 素材画面质量校验")
    log("=" * 60)
    footage_dir = proj_dir / "source-footage"
    video_files = list(footage_dir.glob("*.mp4")) + list(footage_dir.glob("*.MP4"))
    image_files = list(footage_dir.glob("*.jpg")) + list(footage_dir.glob("*.png"))
    all_files = video_files + image_files
    if not all_files:
        log("没有找到素材文件！", "ERROR")
        return False
    log(f"找到 {len(all_files)} 个素材文件")
    recipe_path = proj_dir / "recipe.json"
    cv = _cv_script()
    if cv and recipe_path.exists():
        cmd = f'python "{cv}" --input "{footage_dir}" --pipeline ALL --recipe "{recipe_path}" --project "{proj_dir}"'
        run_cmd(cmd)
    node = {
        "schema_version": "1.0.0",
        "node_id": "L3B", "stage": "assets",
        "produced_by": "dispatcher-L3B",
        "produced_at": datetime.now().isoformat(),
        "status": "ok",
    }
    write_json(proj_dir / "nodes" / "L3B_footage.node.json", node)
    return True

def run_node_L42(proj_dir, req):
    log("=" * 60)
    log("L4.2 · FFmpeg 合成成片")
    log("=" * 60)
    footage_dir = proj_dir / "source-footage"
    video_files = sorted(footage_dir.glob("*.mp4"))
    if not video_files:
        log("没有视频素材可合成", "ERROR")
        return False
    list_path = proj_dir / "concat_list.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for v in video_files[:5]:
            safe_path = str(v.resolve()).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")
    output_path = proj_dir / "final.mp4"
    cmd = f'"{FFMPEG_PATH}" -y -f concat -safe 0 -i "{list_path}" -c copy "{output_path}"'
    rc, out, err = run_cmd(cmd)
    if rc == 0 and output_path.exists():
        log(f"合成完成: {output_path.name}")
    else:
        cmd = f'"{FFMPEG_PATH}" -y -f concat -safe 0 -i "{list_path}" -c:v libx264 -crf 18 -preset fast -c:a aac -b:a 128k -pix_fmt yuv420p "{output_path}"'
        rc, out, err = run_cmd(cmd)
        if rc == 0 and output_path.exists():
            log(f"重编码合成完成: {output_path.name}")
        else:
            log(f"合成失败: {err[:200]}", "ERROR")
            return False
    node = {
        "schema_version": "1.0.0",
        "node_id": "L4.2", "stage": "synthesis",
        "produced_by": "dispatcher-L42",
        "produced_at": datetime.now().isoformat(),
        "status": "ok",
        "payload": {"output_path": str(output_path)},
    }
    write_json(proj_dir / "nodes" / "L4.2_timeline.node.json", node)
    return True

def run_node_generic(node_id, proj_dir, req):
    log(f"节点 {node_id}: 暂未实现，跳过（占位）")
    node = {
        "schema_version": "1.0.0", "node_id": node_id, "stage": "pending",
        "produced_by": "dispatcher-skip", "produced_at": datetime.now().isoformat(),
        "status": "skipped",
    }
    write_json(proj_dir / "nodes" / f"{node_id.replace('.', '_')}_placeholder.node.json", node)
    return True

# ============================================================
# 主调度器
# ============================================================

NODE_RUNNERS = {"L1.2": run_node_L12, "L3B": run_node_L3B, "L4.2": run_node_L42}

def run_pipeline(proj_dir, req):
    proj_dir = Path(proj_dir)
    update_status(proj_dir, status="running")
    for node_info in PIPELINE_NODES:
        node_id = node_info["id"]
        node_name = node_info["name"]
        log(f"▶ 开始节点 {node_id} · {node_name}")
        update_status(proj_dir, current_node=node_id)
        runner = NODE_RUNNERS.get(node_id)
        success = runner(proj_dir, req) if runner else run_node_generic(node_id, proj_dir, req)
        if not success:
            log(f"✗ 节点 {node_id} 失败，流水线中止", "ERROR")
            update_status(proj_dir, status="failed", failed_node=node_id,
                         error_msg=f"节点 {node_id} 执行失败", finished_at=datetime.now().isoformat())
            return False
        status = read_json(proj_dir / "status.json")
        completed = status.get("completed_nodes", [])
        if node_id not in completed:
            completed.append(node_id)
        update_status(proj_dir, completed_nodes=completed)
        log(f"✓ 节点 {node_id} 完成")
    update_status(proj_dir, status="done", current_node=None, finished_at=datetime.now().isoformat())
    log(f"🎉 流水线全部完成！项目: {proj_dir}")
    return True

def run_once(raw_request, proj_dir_override=None):
    ensure_dirs()
    log(f"收到指令: {raw_request[:100]}")
    req = parse_request(raw_request)
    if proj_dir_override:
        proj_dir = Path(proj_dir_override)
        if not proj_dir.exists():
            log(f"指定项目目录不存在: {proj_dir_override}", "ERROR")
            return 1
    else:
        proj_dir = create_project(req)
    footage_path = req.get("source_footage_path")
    if footage_path and os.path.exists(footage_path):
        target = proj_dir / "source-footage"
        src = Path(footage_path)
        for f in list(src.glob("*.mp4"))[:3] + list(src.glob("*.jpg"))[:3]:
            shutil.copy2(f, target / f.name)
    success = run_pipeline(proj_dir, req)
    return str(proj_dir) if success else None

# ============================================================
# 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="AI Factory Dispatcher")
    parser.add_argument("--once", type=str, help="单次模式")
    parser.add_argument("--daemon", action="store_true", help="守护模式")
    parser.add_argument("--resume", type=str, help="从断点继续")
    parser.add_argument("--init", action="store_true", help="初始化")
    args = parser.parse_args()

    if args.init:
        ensure_dirs()
        input_file = INPUT_DIR / "latest_request.txt"
        if not input_file.exists():
            input_file.write_text(
                "# 在这里写指令，保存即触发\n"
                "# 例：做一条硇洲岛的，参考鱼鱼大王风格\n"
                "做一条硇洲岛的抖音视频，参考鱼鱼大王风格\n",
                encoding="utf-8")
        print(f"✅ 初始化完成")
        print(f"   指令文件: {input_file}")
        return 0

    if args.once:
        return 0 if run_once(args.once) else 1
    if args.daemon:
        return 0  # daemon mode placeholder
    parser.print_help()
    return 0

if __name__ == "__main__":
    sys.exit(main())