"""
AI Factory · LLM API 客户端
===========================
基于火山引擎（豆包）OpenAI 兼容接口的 LLM 调用封装。
支持对话补全、带角色人设的系统提示。
"""

import os, json, sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_llm_config():
    """从 config.json 或环境变量加载 LLM 配置"""
    config_path = BASE_DIR / "config" / "config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            llm = cfg.get("llm", {})
            if llm.get("api_key") and llm["api_key"] != "YOUR_API_KEY_HERE":
                return llm
        except Exception:
            pass
    
    return {
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "base_url": os.environ.get(
            "LLM_BASE_URL",
            "https://ark.cn-beijing.volces.com/api/v3"
        ),
        "models": {
            "chat": os.environ.get("LLM_MODEL", ""),
        },
        "provider": "doubao",
    }


def chat(
    system_prompt,
    user_message,
    temperature=0.7,
    max_tokens=2048,
):
    """
    调用豆包 API 获取聊天补全。

    Args:
        system_prompt: 系统提示词（角色人设+任务描述）
        user_message: 用户消息（项目上下文+具体要求）
        temperature: 温度参数 (0.0-1.0)
        max_tokens: 最大生成 token 数

    Returns:
        str: LLM 生成的文本，出错返回 None
    """
    import requests as _req

    config = _load_llm_config()
    api_key = config.get("api_key", "")
    base_url = config.get("base_url", "https://ark.cn-beijing.volces.com/api/v3")
    model = config.get("models", {}).get("chat", "")

    if not api_key or api_key == "YOUR_API_KEY_HERE":
        print("[LLM] 错误: API Key 未配置")
        return None
    if not model or model == "YOUR_CHAT_MODEL_ENDPOINT":
        print("[LLM] 错误: 模型 Endpoint 未配置")
        return None

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        print(f"[LLM] 请求模型: {model}  temperature={temperature}")
        resp = _req.post(url, headers=headers, json=payload, timeout=120)
        if resp.status_code == 200:
            result = resp.json()
            content = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            preview = content[:120].replace("\n", " ")
            print(f"[LLM] 响应 ({len(content)} chars): {preview}...")
            return content
        else:
            body = resp.text[:500]
            print(f"[LLM] API 错误 [{resp.status_code}]: {body}")
            return None
    except Exception as e:
        print(f"[LLM] 请求异常: {e}")
        return None