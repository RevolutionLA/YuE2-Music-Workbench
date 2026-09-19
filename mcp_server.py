# -*- coding: utf-8 -*-
"""音乐工作台 MCP Server（stdio，mcp 2.x API）。

把 ai_tools.py 的 9 个工具暴露为 MCP 工具，供 dsh（或任何 MCP 客户端）
原生调用——带 dsh 的权限审批 UI。启动方式：
    py312/python.exe mcp_server.py
"""
from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

import ai_tools as T

mcp = MCPServer("yue2-lab", instructions="音乐工作台 YuE2 工具集：写词校验、生成、进度、换声、历史、音色管理。")


@mcp.tool()
def list_models() -> str:
    """列出可用音色模型与生成模式说明"""
    return json.dumps(T.tool_list_models(), ensure_ascii=False)


@mcp.tool()
def generate(style: str, lyrics: str, cot: str = "full", steps: int = 32,
             seed: int = -1, count: int = 1) -> str:
    """提交 YuE2 歌曲生成任务。返回 job_id，用 get_progress 查询进度。style 为英文六要素风格标签；lyrics 为 [Verse]/[Chorus] 结构化歌词。"""
    return json.dumps(T.tool_generate(style=style, lyrics=lyrics, cot=cot,
                                      steps=steps, seed=seed, count=count), ensure_ascii=False)


@mcp.tool()
def get_progress() -> str:
    """查询当前生成任务进度与预计剩余时间"""
    return json.dumps(T.tool_get_progress(), ensure_ascii=False)


@mcp.tool()
def rvc_convert(source: str, voice: str, pitch: int = 0) -> str:
    """对已生成的歌曲做 RVC 换声。source=历史任务 id；voice=音色名。"""
    return json.dumps(T.tool_rvc_convert(source=source, voice=voice, pitch=pitch), ensure_ascii=False)


@mcp.tool()
def list_history(limit: int = 10, kind: str = "") -> str:
    """列出最近的生成/换声记录。kind=rvc 只看换声结果。"""
    return json.dumps(T.tool_list_history(limit=limit, kind=kind), ensure_ascii=False)


@mcp.tool()
def get_song(rid: str) -> str:
    """读取某条记录完整参数（style/歌词/seed 等），供修改后重新生成"""
    return json.dumps(T.tool_get_song(rid=rid), ensure_ascii=False)


@mcp.tool()
def validate_lyrics(lyrics: str) -> str:
    """校验歌词结构是否符合 YuE2 规范（段落标签/空行/行数/副歌完整）"""
    return json.dumps(T.tool_validate_lyrics(lyrics=lyrics), ensure_ascii=False)


@mcp.tool()
def delete_history(rid: str) -> str:
    """删除一条历史记录（含音频文件，不可恢复）。首轮返回 needs_confirm，需用户确认后带 confirm=true 重调。"""
    return json.dumps(T.tool_delete_history(rid=rid), ensure_ascii=False)


@mcp.tool()
def delete_voice(name: str) -> str:
    """删除一个音色模型及其索引文件（不可恢复）。首轮返回 needs_confirm，需用户确认后带 confirm=true 重调。"""
    return json.dumps(T.tool_delete_voice(name=name), ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
