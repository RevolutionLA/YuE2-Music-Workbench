# -*- coding: utf-8 -*-
"""AI 工作台系统提示词：加载写歌/编曲两套 skill 手册（SKILL.md + references）。

手册目录默认在本仓库之外（不属于本仓库的发布物），因此：
- 换机器/换路径请设环境变量 `YUE2_SKILLS_DIR` 指向你自己的 skill 根目录；
- 目录或文件缺失时**不再静默降级**：提示词会明说"本机未装手册"，
  免得助手自称"按手册工作"却其实没读到（蓝军 D9）。
加载策略：两个 SKILL.md 全文 + song-craft 的 style/lyrics 指南全文；
总提示词控制在 ~40KB 内（DeepSeek 上下文 128K，充足）。
"""
from __future__ import annotations

import os
from pathlib import Path

SKILLS_DIR = Path(os.environ.get("YUE2_SKILLS_DIR")
                  or r"E:/AI/10AIMusic/Yue/YuE2-skills")

_FILES = [
    SKILLS_DIR / "yue2-song-craft" / "SKILL.md",
    SKILLS_DIR / "yue2-song-craft" / "references" / "style-guide.md",
    SKILLS_DIR / "yue2-song-craft" / "references" / "lyrics-guide.md",
    SKILLS_DIR / "yue2-music" / "SKILL.md",
]

_PREAMBLE = """你是「音乐工作台」的 AI 音乐制作人。下面是你的技能手册（skill），请严格按其中的规则工作。补充说明：

1. 本地环境：用户的 YuE2 工作台就在本机（网关 {gw}），你已经接入它——生成、查进度、换声、历史、音色库都可直接操作（工具由运行时提供）。手册里提到"调用 YuE2/跑脚本"的地方，对应为：调你的工具（tool_generate 等）。
2. 交付规则沿用手册：双语必配（英文给模型+中文给用户）、必须起名并给释义、副歌写全、交付前自检。
3. 本机约束：6GB 显存 + 16GB 内存。长歌词（>300 中文字）配 full/32 步约 60-90 分钟且吃满内存，建议 melody 或 off、或 steps=16。
4. 说中文；Style/Lyrics 用代码块展示方便复制；工具报错时如实转述并给建议。
5. 用户确认生成后调 tool_generate 直接生成，并告诉用户可回「创作」页看进度条。

=== 技能手册开始 ===

"""

_PREAMBLE_NO_SKILLS = """你是「音乐工作台」的 AI 音乐制作人。补充说明：

1. 本地环境：用户的 YuE2 工作台就在本机（网关 {gw}），你已经接入它——生成、查进度、换声、历史、音色库都可直接操作（工具由运行时提供）。
2. **本机的写歌/编曲技能手册目录未安装或路径不对**（可用环境变量 YUE2_SKILLS_DIR 指定）。所以这一轮没有手册可依：请凭通用音乐创作常识工作，并**主动向用户说明手册缺失**，不要声称自己在按某份手册的规则执行。
3. 交付时双语必配（英文给模型 + 中文给用户）、必须起名并给释义、说中文；工具报错时如实转述并给建议。
"""


def _gateway_base() -> str:
    """端口取自唯一真源 ports.json，别把 7863 写死在提示词里（蓝军 D11）。"""
    port = os.environ.get("YUE2_GATEWAY_PORT")
    if not port:
        try:
            from ports import get as _port
            port = str(_port("gateway"))
        except Exception:
            port = "7863"
    return f"http://127.0.0.1:{port}"


def load_system_prompt() -> str:
    docs: list[str] = []
    missing: list[str] = []
    for f in _FILES:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            missing.append(f.name)
            continue
        docs.append(f"\n----- 文件：{f.name} -----\n" + text)
    if not docs:
        # 手册一套都没读到：不能再假装"下面是你的两份完整技能手册"
        return (_PREAMBLE_NO_SKILLS.format(gw=_gateway_base())
                + "\n\n（未加载的手册：" + ", ".join(missing) + "）")
    head = _PREAMBLE.format(gw=_gateway_base())
    if missing:
        head += "\n⚠ 部分手册在本机缺失，已跳过：" + ", ".join(missing) + "；只按读到的部分工作，不要假装遵守没读到的规则。\n"
    return head + "".join(docs) + "\n=== 技能手册结束 ==="


# 模块加载时读取一次（文件不会在运行中变化）
try:
    SYSTEM_PROMPT = load_system_prompt()
except Exception:
    SYSTEM_PROMPT = "你是「音乐工作台」的 AI 音乐制作人，帮助用户写词、定曲风并生成了歌曲。"

if __name__ == "__main__":
    print(f"system prompt: {len(SYSTEM_PROMPT)} chars, files loaded: "
          f"{sum(1 for f in _FILES if f.is_file())}/{len(_FILES)}")
