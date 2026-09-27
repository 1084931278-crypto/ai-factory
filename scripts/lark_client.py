"""
飞书机器人客户端 · lark-cli 版本（轮询模式）
==============================================
用 lark-cli 跟飞书交互，不需要 webhook、不需要公网 IP。
轮询群消息，发现新指令就启动任务。

依赖：
  lark-cli.exe（飞书插件自带，已授权登录）
  用户身份（--as user）

配置方式（三选一，优先级从高到低）：
  1. config.json 中 feishu.lark_cli_path
  2. 环境变量 LARK_CLI_PATH
  3. 在 LARK_CLI 变量下方硬编码
"""

import os
import json
import time
import subprocess
import threading
from pathlib import Path
from datetime import datetime

# 默认路径（按需修改）
LARK_CLI = os.environ.get(
    "LARK_CLI_PATH",
    r"C:\path\to\lark-cli.exe",
)


def _parse_json_output(stdout, stderr):
    """从 lark-cli 输出中解析 JSON（支持多行 JSON）"""
    combined = (stdout or "") + "\n" + (stderr or "")
    start = combined.find("{")
    end = combined.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(combined[start:end+1])
        except json.JSONDecodeError:
            pass
    return None


def _run(args):
    """调用 lark-cli（用参数列表，shell=False，避免转义问题）"""
    cmd = [LARK_CLI] + args
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        return {"ok": False, "error": {"message": f"找不到 lark-cli: {LARK_CLI}"}}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": {"message": "lark-cli 超时"}}

    obj = _parse_json_output(r.stdout, r.stderr)
    if obj is not None:
        return obj

    # 输出里没有 JSON → 当作错误
    msg = (r.stderr or r.stdout or "unknown error").strip()[:300]
    return {"ok": False, "error": {"message": msg}}


# ============================================================
# 消息发送
# ============================================================

def send_text(chat_id, text):
    """发送纯文本消息"""
    return _run([
        "im", "+messages-send",
        "--chat-id", chat_id,
        "--text", text,
        "--as", "user",
    ])


def send_interactive_card(chat_id, card_dict):
    """发送交互卡片"""
    content = json.dumps(card_dict, ensure_ascii=False)
    return _run([
        "im", "+messages-send",
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", content,
        "--as", "user",
    ])


def reply_message(chat_id, message_id, text):
    """回复某条消息"""
    return _run([
        "im", "+messages-reply",
        "--message-id", message_id,
        "--text", text,
        "--as", "user",
    ])


def upload_image(chat_id, image_path):
    """上传图片并发送"""
    upload_result = _run([
        "im", "images", "create",
        "--image-file", image_path,
        "--as", "user",
    ])
    if not upload_result.get("ok"):
        return upload_result
    image_key = upload_result.get("data", {}).get("image_key", "")
    content = json.dumps({"image_key": image_key})
    return _run([
        "im", "+messages-send",
        "--chat-id", chat_id,
        "--msg-type", "image",
        "--content", content,
        "--as", "user",
    ])


# ============================================================
# 消息读取（轮询版）
# ============================================================

def get_messages(chat_id, page_size=20):
    """获取群最新消息（返回列表，兼容 messages/items 两种字段名）"""
    result = _run([
        "im", "+chat-messages-list",
        "--chat-id", chat_id,
        "--page-size", str(page_size),
        "--order", "desc",
        "--as", "user",
    ])
    # 标准化：把消息列表放到 data.items
    if result.get("ok"):
        data = result.get("data", {})
        if "messages" in data and "items" not in data:
            data["items"] = data["messages"]
        result["data"] = data
    return result


class FeishuPoller:
    """
    飞书消息轮询器
    每隔几秒查一次群消息，发现新消息就调用回调函数
    """

    def __init__(self, chat_id, on_message, poll_interval=5, log_fn=None):
        self.chat_id = chat_id
        self.on_message = on_message  # 回调：fn(message_dict)
        self.poll_interval = poll_interval
        self._seen_ids = set()  # 已处理过的消息 ID（去重用）
        self._running = False
        self._thread = None
        self._log = log_fn or print  # 日志函数
        self._poll_count = 0

    def _debug(self, msg):
        self._log(f"[FeishuPoller] {msg}")

    def start(self):
        """启动轮询（后台线程）"""
        if self._running:
            return
        self._running = True
        self._debug("start: begin init")

        # 先拉一次最新消息，全部标记为已读，之后只处理新消息
        try:
            result = get_messages(self.chat_id, page_size=20)
            self._debug(f"start: get_messages ok={result.get('ok')}")
            if result.get("ok"):
                items = result.get("data", {}).get("items", [])
                self._debug(f"start: got {len(items)} items, marking as seen")
                for item in items:
                    mid = item.get("message_id")
                    if mid:
                        self._seen_ids.add(mid)
                self._debug(f"start: seen_ids = {len(self._seen_ids)} messages")
        except Exception as e:
            self._debug(f"start: init error: {e}")

        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._debug(f"启动轮询，chat_id={self.chat_id}, interval={self.poll_interval}s")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _poll_loop(self):
        self._debug("poll_loop: started")
        while self._running:
            try:
                self._poll_count += 1
                self._debug(f"poll #{self._poll_count}: fetching...")
                result = get_messages(self.chat_id, page_size=20)
                ok = result.get("ok")
                self._debug(f"poll #{self._poll_count}: ok={ok}")
                if ok:
                    items = result.get("data", {}).get("items", [])
                    self._debug(f"poll #{self._poll_count}: got {len(items)} items")

                    # 找出未处理过的新消息（不依赖列表顺序）
                    new_messages = []
                    for item in items:
                        mid = item.get("message_id")
                        if mid and mid not in self._seen_ids:
                            new_messages.append(item)
                            self._seen_ids.add(mid)  # 立刻标记，避免重复

                    if new_messages:
                        self._debug(f"poll #{self._poll_count}: {len(new_messages)} new messages")
                        # 按时间从旧到新处理（create_time 升序）
                        new_messages.sort(key=lambda m: m.get("create_time", ""))
                        for msg in new_messages:
                            mid = msg.get("message_id", "?")
                            sender = msg.get("sender", {}).get("name", "?")
                            self._debug(f"poll #{self._poll_count}: processing {mid} from {sender}")
                            try:
                                self.on_message(msg)
                            except Exception as e:
                                self._debug(f"poll #{self._poll_count}: handler error: {e}")
                    else:
                        self._debug(f"poll #{self._poll_count}: no new messages")

                    # 定期清理 seen_ids（防止无限增长，只保留当前页的 ID）
                    if len(self._seen_ids) > 500:
                        current_ids = {i.get("message_id") for i in items if i.get("message_id")}
                        self._seen_ids = current_ids
                        self._debug(f"poll #{self._poll_count}: trimmed seen_ids to {len(self._seen_ids)}")
                else:
                    err = result.get("error", {}).get("message", "unknown")
                    self._debug(f"poll #{self._poll_count}: error: {err}")
            except Exception as e:
                self._debug(f"poll_loop exception: {e}")

            time.sleep(self.poll_interval)
        self._debug("poll_loop: stopped")