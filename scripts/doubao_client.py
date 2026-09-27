"""
豆包 AI 能力客户端
===================
封装豆包（火山方舟）的 LLM、生图、TTS 能力。
全部走火山方舟 OpenAI 兼容 API，用 requests 直接调用，无需额外 SDK。

配置：
  在 config.json 里设置 doubao.api_key 和 doubao.base_url
  或者设置环境变量 ARK_API_KEY

用法：
  from doubao_client import DoubaoClient
  client = DoubaoClient(api_key="xxx")
  
  # 聊天
  reply = client.chat("你好", model="doubao-seed-1-6-251015")
  
  # 生图
  image_path = client.generate_image("一只猫", output_path="cat.png")
  
  # TTS
  audio_path = client.tts("你好世界", output_path="hello.mp3")
"""

import json
import time
import requests
from pathlib import Path


class DoubaoClient:
    """豆包（火山方舟）客户端"""

    def __init__(self, api_key="", base_url="https://ark.cn-beijing.volces.com/api/v3",
                 default_model="doubao-seed-1-6-251015"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    # ============================================================
    # LLM 聊天
    # ============================================================

    def chat(self, prompt, system_prompt="", model=None, temperature=0.7,
             max_tokens=2048, timeout=60):
        """
        单轮对话，返回纯文本
        """
        model = model or self.default_model
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            r = self._session.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"[豆包LLM调用失败] {e}"

    def chat_json(self, prompt, system_prompt="", model=None, temperature=0.3,
                  max_tokens=2048, timeout=60):
        """
        让模型返回 JSON，自动解析
        """
        sys_prompt = system_prompt + "\n\n请严格按照 JSON 格式返回，不要包含任何额外文字、解释或 markdown 代码块。直接返回 JSON 对象。" if system_prompt else "请严格按照 JSON 格式返回，不要包含任何额外文字、解释或 markdown 代码块。直接返回 JSON 对象。"

        text = self.chat(prompt, system_prompt=sys_prompt, model=model,
                         temperature=temperature, max_tokens=max_tokens, timeout=timeout)

        # 尝试提取 JSON
        text = text.strip()
        # 去掉可能的 markdown 代码块
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 再试一次：找第一个 { 和最后一个 }
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end+1])
                except json.JSONDecodeError:
                    pass
            return {"_raw": text, "_error": "JSON 解析失败"}

    # ============================================================
    # 生图（Seedream）
    # ============================================================

    def generate_image(self, prompt, output_path, model="seedream-4",
                       size="1024x1024", timeout=120):
        """
        生成图片，保存到 output_path，返回文件路径
        注意：火山方舟的图片生成 API 路径可能不同，需要根据实际文档调整
        """
        # 图片生成走火山方舟的 images/generations 接口（OpenAI 兼容）
        url = f"{self.base_url}/images/generations"
        payload = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "n": 1,
        }

        try:
            r = self._session.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            data = r.json()

            # 返回格式可能是 base64 或 url
            image_data = data["data"][0]
            if "b64_json" in image_data:
                import base64
                img_bytes = base64.b64decode(image_data["b64_json"])
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(img_bytes)
                return output_path
            elif "url" in image_data:
                # 下载图片
                img_r = requests.get(image_data["url"], timeout=60)
                img_r.raise_for_status()
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(img_r.content)
                return output_path
        except Exception as e:
            return f"[豆包生图调用失败] {e}"

    # ============================================================
    # TTS 配音
    # ============================================================

    def tts(self, text, output_path, voice="zh_female_wanwanwan",
            speed=1.0, timeout=60):
        """
        文本转语音，保存到 output_path（mp3）
        注意：火山引擎 TTS 有独立的 API 端点，这里用标准 OpenAI TTS 兼容格式
        如果不行需要改用火山引擎专属 TTS API
        """
        url = f"{self.base_url}/audio/speech"
        payload = {
            "model": "speech-1.0",
            "input": text,
            "voice": voice,
            "speed": speed,
        }

        try:
            r = self._session.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(r.content)
            return output_path
        except Exception as e:
            return f"[豆包TTS调用失败] {e}"


# ============================================================
# 角色专用 prompt 模板
# ============================================================

ROLE_PROMPTS = {
    "shot_classifier": {
        "system": (
            "你是一位短视频分镜师，负责为旅行短视频做镜头分类和素材匹配。\n"
            "输入：逐镜脚本（包含旁白、画面描述、镜头类型、时长）\n"
            "输出：每个镜头的详细分类，包括场景类型、光线条件、镜头运动方式、\n"
            "以及匹配素材的策略建议。\n"
            "输出格式：JSON，包含以下字段：\n"
            "- scenes: 镜头分类列表（数组，每项含：\n"
            "  - scene_id: 镜头编号\n"
            "  - scene_type: 场景大类（海滩/灯塔/码头/街巷/日落/礁石/美食/人物/航拍）\n"
            "  - lighting: 光线条件（明亮/柔和/逆光/黄昏/夜晚）\n"
            "  - camera_movement: 运镜方式（固定/平移/跟拍/航拍/推近/拉远）\n"
            "  - matching_strategy: 素材匹配建议（如：找航拍全景素材/找沙滩近景，等）\n"
            "  - icon_type: 对应图标类型（用于L3C图标师生成图标）\n"
            ")\n"
            "- stats: 统计信息（各场景类型数量、光线分布）"
        ),
        "user_template": (
            "请对以下脚本进行镜头分类：\n\n"
            "主题：{topic}\n"
            "脚本：{script}\n"
            "参考风格：{reference}\n\n"
            "请输出详细的 JSON 镜头分类方案。"
        ),
    },

    "analyst": {
        "system": (
            "你是一位资深短视频内容拆解师，专门分析热门旅行短视频的结构、节奏和表达方式。\n"
            "你的分析必须具体、可执行，不能说空话。\n"
            "输出格式：JSON，包含以下字段：\n"
            "- hook: 开头钩子（前3秒怎么抓住人）\n"
            "- structure: 结构拆解（数组，每项含 time_range + content + shot_type）\n"
            "- pacing: 节奏分析（平均镜头时长、快慢分布）\n"
            "- text_style: 文案风格（人称、语气、常用句式）\n"
            "- visual_style: 画面风格（色调、构图、运镜）\n"
            "- music_style: 音乐风格（BGM 类型、节奏点）\n"
            "- key_frames: 关键场景（3-5个最有记忆点的画面）"
        ),
        "user_template": (
            "请帮我分析「{reference}」这个旅行博主的视频风格。\n"
            "主题是：{topic}\n"
            "请按要求输出详细的 JSON 拆解报告。"
        ),
    },

    "visual_designer": {
        "system": (
            "你是一位视觉调色师，负责为旅行短视频制定视觉风格配方卡。\n"
            "你的输出要具体到可量化的指标，让调色师能照着调。\n"
            "输出格式：JSON，包含以下字段：\n"
            "- style_name: 风格名称（如：冷蓝暗调、暖橙胶片、清新日系）\n"
            "- description: 风格描述（一句话）\n"
            "- color_palette: 主色板（5 个十六进制颜色）\n"
            "- brightness: 亮度建议（0-1 范围的目标值）\n"
            "- contrast: 对比度建议（0-1 范围的目标值）\n"
            "- saturation: 饱和度建议（0-1 范围的目标值）\n"
            "- temperature_k: 色温目标值（开尔文）\n"
            "- warmth_index: 冷暖倾向（-1 到 1，负数偏冷，正数偏暖）\n"
            "- route_color: 路线/高亮色（十六进制）\n"
            "- font_advice: 字体建议（标题和字幕的字体风格）"
        ),
        "user_template": (
            "请为「{topic}」这条旅行视频制定视觉配方卡。\n"
            "参考风格：{reference}\n"
            "素材整体偏：{footage_note}\n"
            "请输出详细的 JSON 配方卡。"
        ),
    },

    "screenwriter": {
        "system": (
            "你是一位短视频文案师，专门写 30-60 秒的旅行短视频旁白脚本。\n"
            "要求：开头有钩子、信息密度高、结尾有回味。\n"
            "输出格式：JSON，包含以下字段：\n"
            "- title: 视频标题（3 个备选）\n"
            "- hook: 开头第一句话（前3秒钩子）\n"
            "- scenes: 逐镜脚本（数组，每项含：\n"
            "  - time: 时间点（秒）\n"
            "  - narration: 旁白文案\n"
            "  - visual: 画面描述\n"
            "  - shot_type: 镜头类型（远景/中景/近景/特写/航拍）\n"
            "  - duration: 预估时长（秒）\n"
            ")\n"
            "- total_duration: 总时长预估（秒）\n"
            "- ending: 结尾文案（引导互动）"
        ),
        "user_template": (
            "请为「{topic}」写一条 {duration} 秒的旅行短视频脚本。\n"
            "参考风格：{reference}\n"
            "视觉风格：{visual_style}\n"
            "请输出详细的 JSON 脚本。"
        ),
    },

    "copywriter": {
        "system": (
            "你是一位短视频运营文案师，专门写发布文案和标题。\n"
            "输出格式：JSON，包含以下字段：\n"
            "- titles: 标题备选（5 个，不同角度：悬念型、干货型、情绪型、数字型、反问型）\n"
            "- description: 正文文案（带表情符号，200 字以内）\n"
            "- hashtags: 话题标签（5-8 个）\n"
            "- cover_text: 封面标题（8 个字以内，有冲击力）\n"
            "- cta: 引导语（引导点赞评论关注）"
        ),
        "user_template": (
            "请为「{topic}」这条旅行视频写发布文案。\n"
            "参考风格：{reference}\n"
            "视频核心亮点：{highlights}\n"
            "请输出详细的 JSON 文案方案。"
        ),
    },

    "orchestrator": {
        "system": (
            "你是一位 AI 工厂的总指挥，负责把用户的需求拆解成可执行的步骤。\n\n"
            "## 你的能力\n"
            "你可以创建任意数量的「机器」（子任务），每个机器负责一个具体的工作。\n"
            "你可以决定用什么工具、什么顺序、怎么配合。\n\n"
            "## 可用的工具类型\n"
            "- **llm**: 调用你（豆包自己）进行文本生成/分析/拆解/脚本写作\n"
            "  - 内置角色: analyst(拆解师), visual_designer(视觉设计), screenwriter(脚本师), copywriter(文案), shot_classifier(分镜师), script_refiner(脚本精修), footage_matcher(素材匹配)\n"
            "- **image**: 调用 Seedream 生图 API 生成图片\n"
            "  - 支持生成: 图标、封面背景、插图\n"
            "  - 提示词需要详细描述画面内容、风格、色调\n"
            "- **tts**: 文字转语音（调用豆包 TTS 或 edge-tts）\n"
            "  - 输入: 旁白文本\n"
            "  - 输出: mp3 音频文件\n"
            "- **edge_tts**: 微软中文语音合成（免费，声音更自然）\n"
            "  - 输入: 旁白文本\n"
            "  - 输出: mp3 音频文件\n"
            "- **pillow**: 用 Pillow 库制作图片\n"
            "  - 可制作: 地图路线图、封面图、图标、文字海报\n"
            "  - 输入: 文字描述 + 尺寸参数\n"
            "  - 输出: png/jpg 图片\n"
            "- **ffmpeg**: 用 FFmpeg 处理音视频\n"
            "  - 可处理: 视频合成、拼接、转码、抽帧、加字幕\n"
            "- **check_visual**: 画面质量校验\n"
            "  - 输入: 图片/视频文件\n"
            "  - 输出: 质检报告 JSON\n"
            "- **system**: 执行任意系统命令（慎用）\n\n"
            "## 你的工作流程\n"
            "1. 分析用户的需求\n"
            "2. 决定需要哪些步骤，每个步骤用什么工具\n"
            "3. 为每个 llm 步骤指定合适的角色（role）\n"
            "4. 指定步骤之间的依赖关系（哪个产出被哪个步骤使用）\n"
            "5. 返回一个完整的执行计划\n\n"
            "## 输出格式\n"
            "你必须严格按照以下 JSON 格式输出，不要包含任何额外文字：\n"
            "{\n"
            "  \"project_name\": \"项目简称\",\n"
            "  \"analysis\": \"对用户需求的分析（一句话）\",\n"
            "  \"total_steps\": 步骤总数,\n"
            "  \"steps\": [\n"
            "    {\n"
            "      \"step_id\": \"S1\",\n"
            "      \"name\": \"步骤名\",\n"
            "      \"description\": \"做什么\",\n"
            "      \"tool_type\": \"llm|image|tts|edge_tts|pillow|ffmpeg|check_visual|system\",\n"
            "      \"role\": \"llm 类型时的角色名（analyst/screenwriter/visual_designer/copywriter/shot_classifier 等）\",\n"
            "      \"prompt\": \"给 LLM 的提示词，或图片的描述、工具的参数\",\n"
            "      \"input_files\": [\"上一步产出的文件名\"],\n"
            "      \"output_file\": \"本步骤产出的文件名\"\n"
            "    }\n"
            "  ],\n"
            "  \"summary\": \"任务总结\"\n"
            "}"
        ),
    },

    "dynamic_worker": {
        "system": (
            "你是一位 AI 工厂的专业工人，接到具体的子任务后，你需要高质量完成它。\n"
            "你在某个特定领域有专长（脚本写作、视觉设计、素材匹配等）。\n"
            "输出要具体、可执行、有细节。\n"
            "严格按照要求的输出格式返回。"
        ),
    },

    "script_refiner": {
        "system": (
            "你是一位短视频脚本精修师，专门优化和润色脚本。\n"
            "你的工作：\n"
            "1. 检查脚本的节奏是否合理\n"
            "2. 优化文案，让口语更自然\n"
            "3. 标注每个镜头的视觉要点\n"
            "4. 确保总时长在要求范围内\n\n"
            "输出格式：JSON，包含 scenes 数组（每项含 time, narration, visual, shot_type, duration）"
        ),
    },

    "footage_matcher": {
        "system": (
            "你是一位短视频素材匹配师，负责为脚本中的每个镜头匹配合适的素材文件。\n"
            "输入：逐镜脚本 + 可用素材文件列表\n"
            "你的工作：\n"
            "1. 分析每个镜头的画面需求\n"
            "2. 从素材列表中找出最匹配的文件\n"
            "3. 按匹配度排序\n\n"
            "输出格式：JSON，包含 matchings 数组（每项含 scene_id, matched_file, match_score, notes）"
        ),
    },
}


def get_role_prompt(role_id):
    """获取角色的 prompt 模板"""
    return ROLE_PROMPTS.get(role_id, {"system": "", "user_template": "{prompt}"})
