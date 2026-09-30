"""
AI Factory Local · 本地 Web 服务入口
=====================================
一条指令启动，浏览器打开 http://localhost:8765 即可使用。

用法:
  python main.py                    # 启动服务（默认 8765 端口）
  python main.py --port 9000        # 自定义端口
  python main.py --init             # 初始化配置
"""

import os, sys, json, threading, queue, traceback, webbrowser
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_from_directory

# 控制台编码兜底：日志里的 emoji 在 GBK 控制台 / 被重定向(管道、日志文件、AI 工具终端)
# 时会触发 UnicodeEncodeError 直接让进程崩溃。保留原编码，只把无法编码的字符替换掉。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

# 把当前目录加入 path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# ============================================================
# 配置加载
# ============================================================

def load_config():
    config_path = BASE_DIR / "config" / "config.json"
    example_path = BASE_DIR / "config" / "config.example.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    elif example_path.exists():
        with open(example_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"server": {"host": "0.0.0.0", "port": 8765, "debug": False}}

CFG = load_config()

# Railway / 云环境变量覆盖
_env_port = os.environ.get("PORT")
if _env_port:
    CFG.setdefault("server", {})["port"] = int(_env_port)

# 环境变量覆盖 LLM 配置（Railway 上通过 Dashboard 设置）
_llm_key = os.environ.get("LLM_API_KEY")
_llm_url = os.environ.get("LLM_BASE_URL")
_llm_model = os.environ.get("LLM_MODEL")
if _llm_key:
    CFG.setdefault("llm", {})["api_key"] = _llm_key
if _llm_url:
    CFG.setdefault("llm", {})["base_url"] = _llm_url
if _llm_model:
    CFG.setdefault("llm", {}).setdefault("models", {})["chat"] = _llm_model

SERVER_CFG = CFG.get("server", {})
HOST = SERVER_CFG.get("host", "0.0.0.0")
PORT = SERVER_CFG.get("port", 8765)

# ============================================================
# Flask 应用
# ============================================================

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config["JSON_AS_ASCII"] = False

# 导入调度器
from dispatcher import (
    run_once, create_project, parse_request, read_json, write_json,
    PROJECTS_DIR, LOGS_DIR, log, run_pipeline, update_status
)

# 全局状态
task_queue = queue.Queue()
active_tasks = {}
active_tasks_lock = threading.Lock()

# ============================================================
# API 路由
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def api_status():
    """系统状态"""
    projects = []
    if PROJECTS_DIR.exists():
        for p in sorted(PROJECTS_DIR.iterdir(), key=lambda x: x.name, reverse=True)[:20]:
            if p.is_dir() and (p / "status.json").exists():
                try:
                    s = read_json(p / "status.json")
                    projects.append({
                        "name": p.name,
                        "status": s.get("status", "unknown"),
                        "current_node": s.get("current_node", ""),
                        "completed_nodes": len(s.get("completed_nodes", [])),
                        "total_nodes": 11,
                        "started_at": s.get("started_at", ""),
                        "finished_at": s.get("finished_at", ""),
                    })
                except:
                    pass

    return jsonify({
        "ok": True,
        "server": f"http://{HOST}:{PORT}",
        "config_loaded": bool(CFG.get("llm", {}).get("api_key")),
        "projects": projects,
        "total_projects": len(projects),
    })

@app.route("/api/projects")
def api_projects():
    """项目列表"""
    projects = []
    if PROJECTS_DIR.exists():
        for p in sorted(PROJECTS_DIR.iterdir(), key=lambda x: x.name, reverse=True):
            if p.is_dir() and (p / "status.json").exists():
                try:
                    s = read_json(p / "status.json")
                    req = read_json(p / "request.json") if (p / "request.json").exists() else {}
                    projects.append({
                        "name": p.name,
                        "topic": req.get("topic", ""),
                        "status": s.get("status", "unknown"),
                        "current_node": s.get("current_node", ""),
                        "completed_nodes": s.get("completed_nodes", []),
                        "started_at": s.get("started_at", ""),
                        "finished_at": s.get("finished_at", ""),
                        "has_final": (p / "final.mp4").exists(),
                    })
                except:
                    pass
    return jsonify({"ok": True, "projects": projects})

@app.route("/api/project/<name>")
def api_project(name):
    """项目详情"""
    p = PROJECTS_DIR / name
    if not p.exists() or not (p / "status.json").exists():
        return jsonify({"ok": False, "error": "项目不存在"}), 404
    try:
        status = read_json(p / "status.json")
        req = read_json(p / "request.json") if (p / "request.json").exists() else {}
        nodes = []
        if (p / "nodes").exists():
            for nf in sorted((p / "nodes").iterdir()):
                try:
                    nodes.append(read_json(nf))
                except:
                    pass
        assets = []
        if (p / "assets").exists():
            assets = [f.name for f in (p / "assets").iterdir() if f.is_file()]
        return jsonify({
            "ok": True,
            "project": {
                "name": name,
                "status": status,
                "request": req,
                "nodes": nodes,
                "assets": assets,
                "has_final": (p / "final.mp4").exists(),
            }
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/trigger", methods=["POST"])
def api_trigger():
    """触发新任务"""
    data = request.get_json(force=True) or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"ok": False, "error": "请输入指令"}), 400
    
    # 异步执行
    def worker():
        try:
            proj_path = run_once(text)
            if proj_path:
                log(f"任务完成: {proj_path}")
        except Exception as e:
            log(f"任务执行出错: {e}", "ERROR")
            log(traceback.format_exc(), "ERROR")
    
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    
    return jsonify({"ok": True, "message": "任务已提交，请查看项目列表"})

@app.route("/api/logs")
def api_logs():
    """获取最新日志"""
    log_file = LOGS_DIR / f"{datetime.now().strftime('%Y%m%d')}.log"
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()[-100:]  # 最后 100 行
        return jsonify({"ok": True, "logs": lines})
    return jsonify({"ok": True, "logs": []})

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """读取/更新配置"""
    config_path = BASE_DIR / "config" / "config.json"
    if request.method == "POST":
        data = request.get_json(force=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        global CFG
        CFG = data
        return jsonify({"ok": True, "message": "配置已更新"})
    return jsonify({"ok": True, "config": CFG})

@app.route("/projects/<name>/<path:filename>")
def serve_project_file(name, filename):
    p = PROJECTS_DIR / name
    if not p.exists():
        return "Not found", 404
    return send_from_directory(str(p.resolve()), filename)

@app.route("/workbench")
def workbench():
    return send_from_directory(str(BASE_DIR), 'workbench.html')

# ============================================================
# 入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI Factory Local Server")
    parser.add_argument("--port", type=int, default=PORT, help="端口号")
    parser.add_argument("--host", type=str, default=HOST, help="监听地址")
    parser.add_argument("--init", action="store_true", help="初始化目录结构")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    if args.init:
        from dispatcher import ensure_dirs
        ensure_dirs()
        input_dir = BASE_DIR / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        input_file = input_dir / "latest_request.txt"
        if not input_file.exists():
            input_file.write_text(
                "# AI Factory · 指令输入文件\n"
                "# 修改这个文件并保存，就会触发一条新任务\n"
                "#\n"
                "# 示例：做一条硇洲岛的抖音视频，参考鱼鱼大王风格\n"
                "\n"
                "做一条硇洲岛的抖音视频，参考鱼鱼大王风格\n",
                encoding="utf-8")
        print("✅ 初始化完成")
        return 0

    host = args.host
    port = args.port

    print("=" * 56)
    print("  AI Factory · 本地 Web 服务")
    print("=" * 56)
    print()
    print(f"  📍 本地地址: http://localhost:{port}")
    print(f"  📍 局域网地址: http://{_get_local_ip()}:{port}")
    print()
    print("  在浏览器打开即可使用")
    print("  Ctrl+C 停止服务")
    print("=" * 56)

    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    app.run(host=host, port=port, debug=False, use_reloader=False)

def _get_local_ip():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

if __name__ == "__main__":
    sys.exit(main())