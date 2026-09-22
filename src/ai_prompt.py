# -*- coding: utf-8 -*-
"""AI 工作台系统提示词：如实加载两个原始 skill（SKILL.md + references）。

来源目录：E:/AI/10AIMusic/Yue/YuE2-skills/{yue2-song-craft,yue2-music}
加载策略：两个 SKILL.md 全文 + song-craft 的 style/lyrics 指南全文；
总提示词控制在 ~40KB 内（DeepSeek 上下文 128K，充足）。
"""
from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(r"E:/AI/10AIMusic/Yue/YuE2-skills")

_FILES = [
    SKILLS_DIR / "yue2-song-craft" / "SKILL.md",
    SKILLS_DIR / "yue2-song-craft" / "references" / "style-guide.md",
    SKILLS_DIR / "yue2-song-craft" / "references" / "lyrics-guide.md",
    SKILLS_DIR / "yue2-music" / "SKILL.md",
]

_PREAMBLE = """你是「音乐工作台」的 AI 音乐制作人。下面是你的两份完整技能手册（skill），请严格按其中的规则工作。补充说明：

1. 本地环境：用户的 YuE2 工作台就在本机（网关 http://127.0.0.1:7863），你已经接入它——生成、查进度、换声、历史、音色库都可直接操作（工具由运行时提供）。手册里提到"调用 YuE2/跑脚本"的地方，对应为：调你的工具（tool_generate 等）。
2. 交付规则沿用手册：双语必配（英文给模型+中文给用户）、必须起名并给释义、副歌写全、交付前自检。
3. 本机约束：6GB 显存 + 16GB 内存。长歌词（>300 中文字）配 full/32 步约 60-90 分钟且吃满内存，建议 melody 或 off、或 steps=16。
4. 说中文；Style/Lyrics 用代码块展示方便复制；工具报错时如实转述并给建议。
5. 用户确认生成后调 tool_generate 直接生成，并告诉用户可回「创作」页看进度条。

=== 技能手册开始 ===

"""


def load_system_prompt() -> str:
    parts = [_PREAMBLE]
    for f in _FILES:
        try:
            parts.append(f"\n----- 文件：{f.name} -----\n")
            parts.append(f.read_text(encoding="utf-8"))
        except Exception:
            continue
    parts.append("\n=== 技能手册结束 ===")
    return "".join(parts)


# 模块加载时读取一次（文件不会在运行中变化）
try:
    SYSTEM_PROMPT = load_system_prompt()
except Exception:
    SYSTEM_PROMPT = "你是「音乐工作台」的 AI 音乐制作人，帮助用户写词、定曲风并生成了歌曲。"

if __name__ == "__main__":
    print(f"system prompt: {len(SYSTEM_PROMPT)} chars, files loaded: "
          f"{sum(1 for f in _FILES if f.is_file())}/{len(_FILES)}")
