# AI Factory · 本地 Web 控制台

一条指令就能跑完整条视频制作流水线的 AI 工厂。浏览器打开即可使用。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API
# 复制 config/config.example.json 为 config/config.json
# 填入你的豆包/火山方舟 API Key 和模型 Endpoint

# 3. 启动服务
python main.py
```

浏览器打开 **http://localhost:8765**

## 文件结构

```
ai-factory-local/
├── main.py               # Web 服务入口（启动这个）
├── dispatcher.py          # 流水线调度器
├── scripts/               # 核心 Python 脚本
│   ├── pipeline_lib.py    # 画面分析库
│   ├── check_visual.py    # 画面质量校验
│   ├── doubao_client.py   # 豆包 AI 客户端
│   ├── lark_client.py     # 飞书客户端
│   └── roles.py           # AI 角色人设
├── config/
│   ├── config.example.json # 配置模板
│   └── recipe.default.json # 默认配方卡
├── kit/
│   ├── config/            # 配方卡配置
│   └── schemas/           # 合约 Schema
├── templates/
│   └── index.html         # Web 界面
├── projects/              # 项目文件（自动生成）
├── input/                 # 指令输入
├── requirements.txt        # Python 依赖
└── README.md
```

## API 接口

| 路径 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Web 控制台 |
| `/api/status` | GET | 系统状态 |
| `/api/projects` | GET | 项目列表 |
| `/api/project/<name>` | GET | 项目详情 |
| `/api/trigger` | POST | 提交新任务 |
| `/api/config` | GET/POST | 读取/更新配置 |
| `/api/logs` | GET | 运行日志 |

## 配置说明

在 `config/config.json` 中配置：

```json
{
  "llm": {
    "api_key": "你的火山方舟 API Key",
    "models": {
      "chat": "你的对话模型 Endpoint"
    }
  },
  "factory": {
    "ffmpeg_path": "ffmpeg",
    "source_footage_root": ""
  }
}
```

- `api_key`: 火山方舟的 API Key（必填）
- `models.chat`: 对话模型 Endpoint ID（必填）
- `ffmpeg_path`: ffmpeg 路径，默认用系统 PATH 里的
- `source_footage_root`: 素材根目录（可选）

## 在其他 AI 中使用

把这个 `ai-factory-local` 文件夹放到另一个 AI 编程工具（Cursor / Windsurf / Claude Code 等）的工作目录下，让 AI 读取 `README.md` 了解系统结构，然后通过 `http://localhost:8765` 的 API 接口交互即可。所有配置都是占位符，没有硬编码路径和敏感信息。