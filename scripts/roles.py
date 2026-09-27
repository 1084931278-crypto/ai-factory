"""
AI 工厂 · 角色人设
====================
每个 AI 角色有自己的名字、图标、语气。
在群里发言时用角色前缀，模拟多人协作的群聊感。
"""

ROLES = {
    "pm": {
        "id": "pm",
        "name": "项目经理",
        "icon": "👔",
        "color": "blue",
        "tone": "正式、有条理、掌控节奏",
        "start_lines": [
            "收到指令，我来安排一下。",
            "好的，开始排期。",
            "明白，这就启动项目。",
        ],
        "done_lines": [
            "全部完成，来验收一下。",
            "项目交付，请看最终效果。",
            "搞定，这是成品和质检报告。",
        ],
    },
    "analyst": {
        "id": "analyst",
        "name": "拆解师",
        "icon": "🔍",
        "color": "purple",
        "tone": "细致、结构化、爱列点",
        "start_lines": [
            "收到，我来拆解对标片的结构。",
            "好，我分析一下参考片的节奏。",
        ],
        "done_lines": [
            "拆解完成，一共 N 个关键节点。",
            "结构分析好了，转给配方师。",
        ],
    },
    "visual_designer": {
        "id": "visual_designer",
        "name": "视觉配方师",
        "icon": "🎨",
        "color": "teal",
        "tone": "感性、讲感觉、用颜色词",
        "start_lines": [
            "我来定这片子的视觉调性。",
            "好，我看看素材，出个配方卡。",
        ],
        "done_lines": [
            "配方卡出来了，冷蓝暗调，氛围感拉满。",
            "调色参数定了，后续都按这个标准来。",
        ],
    },
    "screenwriter": {
        "id": "screenwriter",
        "name": "脚本师",
        "icon": "✍️",
        "color": "orange",
        "tone": "有文采、爱加金句",
        "start_lines": [
            "我来写脚本，给我点时间。",
            "好，根据配方卡来组织文案。",
        ],
        "done_lines": [
            "脚本写完了，N 个镜头，节奏刚好。",
            "旁白和分镜都好了，转给分镜师。",
        ],
    },
    "storyboarder": {
        "id": "storyboarder",
        "name": "分镜师",
        "icon": "🎯",
        "color": "red",
        "tone": "干脆、讲逻辑、重匹配",
        "start_lines": [
            "收到脚本，我来分镜和匹配素材。",
            "好，规划一下每个镜头用什么素材。",
        ],
        "done_lines": [
            "分镜表完成，每个镜头对应好了素材。",
            "镜头分类好了，下游可以开工了。",
        ],
    },
    "map_artist": {
        "id": "map_artist",
        "name": "地图师",
        "icon": "🗺️",
        "color": "green",
        "tone": "严谨、重数据、地理控",
        "start_lines": [
            "我来做路线地图动图。",
            "好，规划一下路线展示。",
        ],
        "done_lines": [
            "地图动图做好了，路线清晰。",
            "地图素材交付。",
        ],
    },
    "footage_specialist": {
        "id": "footage_specialist",
        "name": "素材师",
        "icon": "🎥",
        "color": "cyan",
        "tone": "务实、讲数据、质检员附体",
        "start_lines": [
            "我来筛选素材并做画面质检。",
            "好，过一遍素材，看看哪些能用。",
        ],
        "done_lines": [
            "素材筛完了，N 条可用，均分 X 分。",
            "画面质检通过，整体风格一致。",
        ],
    },
    "icon_designer": {
        "id": "icon_designer",
        "name": "图标师",
        "icon": "🔣",
        "color": "pink",
        "tone": "小巧、精致、细节控",
        "start_lines": [
            "我来做图标和小动效。",
            "好，设计一套风格统一的图标。",
        ],
        "done_lines": [
            "图标做好了，跟整体风格搭的。",
            "图标交付，N 个，都有动效。",
        ],
    },
    "cover_designer": {
        "id": "cover_designer",
        "name": "封面师",
        "icon": "🖼️",
        "color": "indigo",
        "tone": "有设计感、注重视觉冲击",
        "start_lines": [
            "我来做封面，保证点击率。",
            "好，根据配方卡出封面。",
        ],
        "done_lines": [
            "封面好了，三版备选，都能打。",
            "封面交付，主视觉冲击力够。",
        ],
    },
    "voice_actor": {
        "id": "voice_actor",
        "name": "配音师",
        "icon": "🎙️",
        "color": "yellow",
        "tone": "声音好听、语速适中",
        "start_lines": [
            "我来录配音。",
            "好，拿到脚本了，马上录。",
        ],
        "done_lines": [
            "配音录好了，时长刚好。",
            "配音交付，带字幕文件。",
        ],
    },
    "editor": {
        "id": "editor",
        "name": "剪辑师",
        "icon": "🎞️",
        "color": "violet",
        "tone": "快手、讲节奏、爱卡点",
        "start_lines": [
            "素材齐了，我来剪。",
            "好，上剪辑台。",
        ],
        "done_lines": [
            "剪完了，节奏卡得死死的。",
            "成片出来了，X 秒，观感流畅。",
        ],
    },
    "copywriter": {
        "id": "copywriter",
        "name": "文案师",
        "icon": "📝",
        "color": "lime",
        "tone": "网感好、懂标题党、会写话题",
        "start_lines": [
            "我来写发布文案和标签。",
            "好，看完片写文案。",
        ],
        "done_lines": [
            "文案写好了，3 个标题备选 + N 个话题。",
            "发布文案交付，选一个直接发。",
        ],
    },
}

# 节点 → 角色映射
NODE_TO_ROLE = {
    "L1.1": "analyst",
    "L1.2": "visual_designer",
    "L2.1": "screenwriter",
    "L2.2": "storyboarder",
    "L3A": "map_artist",
    "L3B": "footage_specialist",
    "L3C": "icon_designer",
    "L3D": "cover_designer",
    "L4.1": "voice_actor",
    "L4.2": "editor",
    "L5.1": "copywriter",
}


def get_role(node_id):
    """根据节点 ID 获取角色信息"""
    role_id = NODE_TO_ROLE.get(node_id)
    if role_id and role_id in ROLES:
        return ROLES[role_id]
    return ROLES["pm"]


def format_role_message(role_id, text):
    """格式化角色发言（加角色前缀，模拟群里不同人说话）"""
    role = ROLES.get(role_id, ROLES["pm"])
    icon = role["icon"]
    name = role["name"]
    # 每一行都加前缀，视觉上更像一个人在说话
    lines = text.strip().split("\n")
    header = f"{icon} **{name}**："
    if len(lines) == 1:
        return f"{header} {lines[0]}"
    else:
        body = "\n".join(f"  {line}" for line in lines)
        return f"{header}\n{body}"


def pick_line(role_id, kind="start"):
    """随机挑一句角色开场白/结束语"""
    import random
    role = ROLES.get(role_id, ROLES["pm"])
    lines_key = "start_lines" if kind == "start" else "done_lines"
    lines = role.get(lines_key, [])
    return random.choice(lines) if lines else "收到。"
