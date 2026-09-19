<div align="center">

# 🎵 音乐工作台 YuE2

**一键开唱 · 本地 AI 音乐生成工作站**

写词 + 写曲 + 演唱，全流程在你自己的电脑上完成 —— 不上传、不排队、不花钱。

`YuE2 大模型` · `GGUF 量化推理` · `翻唱改词` · `RVC 换声` · `批量生成` · `大陆网络开箱即用`

</div>

---

## ✨ 它能做什么

| | 功能 | 一句话说明 |
|---|---|---|
| 🎼 | **AI 写歌** | 输入风格 + 歌词 → 完整人声歌曲（副歌/主歌自动编排），支持改旋律（ABC 乐谱）|
| 🎤 | **翻唱 / 改词 / 改曲风** | 丢一段参考音频 → 自动识别歌词 → 换词换曲风重新演绎 |
| 🗣 | **换声** | RVC 音色转换：把自己的音色变成任意歌手 |
| 🎨 | **音色制作** | 上传干声训练专属音色库 |
| 📦 | **批量生成** | 一次排队几十首，跑完自动落盘，**中途可随时终止** |
| 📥 | **历史管理** | 每首歌自动存档，随时回填参数重跑、一键下载 WAV |
| ⏸ | **任务托管** | 刷新页面、关掉浏览器都不丢任务，回来接着看 |
| 🖥 | **显存自适应** | 显存够走 GPU 飞快，不够自动切 CPU 兜底，完成后自动切回 |

## 🚀 三步开唱

> **Windows x64 / 16GB+ 内存 / 建议 6GB+ 显存**（无独显也能跑，自动走 CPU，慢一些）

```bat
:: 1. 下载本仓库（Green 一键解压也行）
git clone https://github.com/RevolutionLA/音乐工作台-YuE2.git

:: 2. 双击启动
scripts\启动音乐工作台.bat

:: 3. 浏览器自动打开工作台 → 填风格和歌词 → 点「生成歌曲」
```

**第一次运行会自动下载模型吗？会！** 启动脚本检测到模型缺失时，自动从 **hf-mirror.com 大陆镜像**（约 2.7GB，支持断点续传）下载 YuE2-3B GGUF 模型 + VAE，下载完直接开唱。中断了？重新双击启动脚本，接着下。

<details>
<summary><b>🌐 大陆网络加速说明（点开）</b></summary>

- 模型下载默认走 `https://hf-mirror.com`（大陆直连，无需科学上网）
- 镜像不可用时自动回退 `huggingface.co`
- 想手动指定镜像：设置环境变量 `HF_ENDPOINT=https://hf-mirror.com`
- 想手动下载 / 换量化版本（q8 更高质量约 4GB）：

```bat
py312\python.exe scripts\download_models.py          :: q4_k_m（默认，约2.7GB）
py312\python.exe scripts\download_models.py --q8     :: q8_0（约4GB，质量更佳）
```

</details>

## 🏗 架构

```
浏览器 ── 3081 一体化工作台（dsh-plugin）
              │ /lab-api/* 反代
              ▼
        7863 FastAPI 网关（app.py：任务托管/批量队列/终止/换声/音色训练）
              │
              ▼
        8080 audio.cpp 推理引擎（YuE2 GGUF，CUDA/CPU 自适应）
```

| 目录 | 内容 |
|---|---|
| `app.py` | 网关扩展路由：任务托管 / 批量排队 / 终止 / 模型切换 / 换声 |
| `static/` | 创作页前端（明暗主题，刷新不丢状态） |
| `dsh-plugin/` | 一体化工作台 UI 插件 |
| `scripts/` | 启动/停止/模型下载脚本、ASR 与降噪辅助 |
| `watchdog.py` | 网关看门狗（假死自愈、无窗口静默运行） |
| `cpp/` | YuE2 GGUF 推理引擎与模型（模型不入库，自动下载） |
| `checkpoints/` | SheetSage2 乐谱提取（含上游许可） |

## 🙏 致谢

- [m-a-p/YuE2](https://huggingface.co/m-a-p/YuE2-3B) —— 音乐生成模型
- [audio.cpp](https://github.com/leejet/audio.cpp) · [GGUF 量化](https://huggingface.co/ngquocvinh/YuE2-3B-GGUF) —— 本地推理引擎
- RVC / SheetSage2 / SenseVoice —— 换声、乐谱、语音识别

## 📄 许可

本仓库扩展代码随各上游组件许可分发；模型权重遵循 [m-a-p/YuE2](https://huggingface.co/m-a-p/YuE2-3B) 原始许可（CC-BY-NC 4.0，**非商用**）。使用/二次分发前请核对 `checkpoints/` 内许可与 `THIRD_PARTY_NOTICES`。

---

<div align="center">

**觉得有用就点个 ⭐ 吧！**

</div>
