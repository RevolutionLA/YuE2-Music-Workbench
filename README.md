<div align="center">

<img src="pic/音乐工作台.png" alt="音乐工作台 YuE2" width="860">

# 🎵 音乐工作台 YuE2

**一键开唱 · 本地 AI 音乐生成工作站**

_写词 + 写曲 + 演唱 + 逐字卡拉OK歌词，全流程在你自己的电脑上完成 —— 不上传、不排队、不花钱。_

[![License: CC-BY-NC-4.0](https://img.shields.io/badge/License-CC--BY--NC--4.0-red.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20x64-0078D6.svg?logo=windows11&logoColor=white)](#-三步开唱)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-%E2%9A%A1-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![CUDA](https://img.shields.io/badge/CUDA-GPU%2FCPU%20自适应-76B900.svg?logo=nvidia&logoColor=white)](#-常见问题)
[![GGUF](https://img.shields.io/badge/GGUF-量化推理-8A2BE2.svg)](https://github.com/ggml-org/ggml)
[![RVC](https://img.shields.io/badge/RVC-音色转换-FF6B9D.svg)](#-它能做什么)
[![Forced Alignment](https://img.shields.io/badge/歌词-逐字强制对齐-FF9F43.svg)](#-它能做什么)
[![Offline](https://img.shields.io/badge/100%25-本地运行-success.svg?logo=shield&logoColor=white)](#-常见问题)
[![Stars](https://img.shields.io/github/stars/RevolutionLA/YuE2-Music-Workbench?style=social)](https://github.com/RevolutionLA/YuE2-Music-Workbench/stargazers)
[![Forks](https://img.shields.io/github/forks/RevolutionLA/YuE2-Music-Workbench?style=social)](https://github.com/RevolutionLA/YuE2-Music-Workbench/network/members)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/RevolutionLA/YuE2-Music-Workbench/pulls)

**`YuE2 大模型`** · **`GGUF 量化`** · **`AI 翻唱`** · **`RVC 换声`** · **`逐字卡拉OK歌词`** · **`批量生成`** · **`断点续跑`** · **`大陆网络开箱即用`**

[快速开始](#-三步开唱) · [功能一览](#-它能做什么) · [界面预览](#-界面预览) · [架构](#-架构) · [常见问题](#-常见问题) · [致谢](#-致谢)

**⭐ 觉得有用就点个 Star —— 这是给独立开发者最好的鼓励！**

</div>

---

## 📖 目录

- [✨ 它能做什么](#-它能做什么)
- [🎬 界面预览](#-界面预览)
- [🚀 三步开唱](#-三步开唱)
- [🏗 架构](#-架构)
- [❓ 常见问题](#-常见问题)
- [🙏 致谢](#-致谢)
- [📄 许可](#-许可)

## ✨ 它能做什么

| | 功能 | 一句话说明 |
|---|---|---|
| 🎼 | **AI 写歌** | 输入风格 + 歌词 → 完整人声歌曲（副歌/主歌自动编排），支持改旋律（ABC 乐谱）|
| 🤖 | **AI 辅助写词写风格** | 内置 DeepSeek 驱动的创作助手：说人话，它帮你出六要素风格标签和结构化歌词 |
| 🎤 | **翻唱 / 改词 / 改曲风** | 丢一段参考音频 → 自动识别歌词 → 换词换曲风重新演绎 |
| 🗣 | **换声** | RVC 音色转换：把自己的音色变成任意歌手 |
| 🎤⭐ | **逐字卡拉OK歌词** | 歌词强制对齐（非 ASR 识别）：中文 FunASR 字级时间戳 + 英文 wav2vec2 CTC 对齐 + VAD 起音锚点校正，产出标准 `.lrc` 与逐字高亮的增强 `.elrc` |
| 🎨 | **音色制作** | 上传干声训练专属音色库：完整歌曲自动分离人声、**断点续跑**、训练前模型体检、多音色融合、训练过程试听 |
| 📦 | **批量生成** | 一次排队几十首，跑完自动落盘，**中途可随时终止** |
| 📥 | **历史管理** | 每首歌自动存档，随时回填参数重跑、一键下载 WAV |
| ⏸ | **任务托管** | 暂停/续跑状态机加固：刷新页面、关掉浏览器、甚至重启服务都不丢任务 |
| 🩺 | **看门狗自愈** | 网关 + 工作台双进程假死检测（兼容 token 鉴权的 401 探活），自动击杀并拉起，页面卡不住 |
| 🧩 | **端口唯一真源** | 所有服务端口集中在 `ports.json` 一处，改端口只改一个文件；支持环境变量临时覆盖 |
| 🖥 | **显存自适应** | 显存够走 GPU 飞快，不够自动切 CPU 兜底，完成后自动切回；试听/转换前置内存闸门，与训练互不挤死 |

## 🎬 界面预览

<div align="center">

### 创作主界面
<img src="pic/音乐工作台.png" alt="创作主界面" width="860">

### 🤖 AI 辅助写词写风格
<img src="pic/音乐工作台-AI辅助写词写风格.png" alt="AI 辅助写词" width="860">

### ⏸ 任务管理
<img src="pic/音乐工作台-任务管理.png" alt="任务管理" width="860">

| 🎤 换声 | 📦 批量生成 | 🎨 音色制作 |
|:---:|:---:|:---:|
| <img src="pic/预览效果图-换声.png" width="270" alt="换声"> | <img src="pic/预览效果图-批量.png" width="270" alt="批量生成"> | <img src="pic/预览效果图-音色制作.png" width="270" alt="音色制作"> |

### ⚙️ dsh 原生配置模型
<img src="pic/音乐工作台-dsh原生配置模型.png" alt="dsh 配置" width="720">

</div>

## 🚀 三步开唱

> **Windows x64 / 16GB+ 内存 / 建议 6GB+ 显存**（无独显也能跑，自动走 CPU，慢一些）

```bat
:: 1. 下载本仓库（Green 一键解压也行）
git clone https://github.com/RevolutionLA/YuE2-Music-Workbench.git

:: 2. 双击「启动音乐工作台.bat」（根目录或 scripts/ 下均可）

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

<details>
<summary><b>🔧 想改端口？（点开）</b></summary>

所有服务端口集中在仓库根的 `ports.json`，改完重启生效：

```json
{
  "gateway": 7863,    // FastAPI 网关
  "dsh": 3081,        // 工作台 UI
  "audiocpp": 8080    // 推理引擎
}
```

也可用环境变量临时覆盖（优先级更高，便于排障/并行实例）：
`YUE2_GATEWAY_PORT` / `YUE2_DSH_PORT` / `YUE2_AUDIOCPP_PORT`

文件缺失或写坏时自动回退默认端口——配置问题绝不会导致服务起不来。

</details>

## 🏗 架构

```
浏览器 ── 3081 工作台 UI（dsh-plugin，UI 唯一入口）
              │ /lab-api/* 反代
              ▼
        7863 FastAPI 网关（app.py：纯 API，任务托管/批量/换声/音色训练/歌词对齐）
              │
              ├── 8080 audio.cpp 推理引擎（YuE2 GGUF，CUDA/CPU 自适应）
              ├── src/lrc_align.py 歌词强制对齐（FunASR / wav2vec2 CTC / VAD 锚点）
              ├── RVC（换声 / 音色训练：分离人声、断点续跑、融合、试听）
              └── watchdog（双进程假死自愈守护）
```

> 🔩 **端口唯一真源**：`ports.json` 一处配置，Python（settings/app/watchdog）、Node（dsh-plugin）、bat 启动脚本全部同源读取。

| 目录 | 内容 |
|---|---|
| `app.py` | 网关扩展路由：任务托管 / 批量排队 / 终止 / 模型切换 / 换声 / 歌词对齐 |
| `src/lrc_align.py` | 歌词强制对齐引擎（字/词级时间戳 → LRC + 增强 ELRC 卡拉OK逐字高亮） |
| `src/ports.py` | 端口唯一真源（ports.json 读取 + 环境变量覆盖 + 缺失回退） |
| `ports.json` | 服务端口配置（改端口只改这一处） |
| `static/` | 创作页前端（明暗主题，刷新不丢状态） |
| `dsh-plugin/` | 工作台 UI 插件（3081 唯一 UI 入口） |
| `scripts/` | 启动/停止/模型下载/dsh 工作台启动脚本、ASR 与降噪辅助 |
| `watchdog.py` | 看门狗（网关 + 工作台双进程假死自愈，无窗口静默运行） |
| `cpp/` | YuE2 GGUF 推理引擎与模型（模型不入库，自动下载） |
| `checkpoints/` | SheetSage2 乐谱提取（含上游许可） |
| `pic/` | README 截图 |

## ❓ 常见问题

<details>
<summary><b>没有 NVIDIA 显卡能跑吗？</b></summary>

能。显存不足或无独显时自动切换 CPU 后端（速度慢约 5-10 倍），生成完成后自动切回。16GB 内存 + 现代多核 CPU 即可出歌，只是等待时间更长。
</details>

<details>
<summary><b>生成一首歌要多久？</b></summary>

full 模式（可编辑旋律 + 和声）+ 32 步 + 长歌词，6GB+ 显存约 60-90 分钟；melody / off 模式更快。支持批量排队挂机。
</details>

<details>
<summary><b>数据会上传到云端吗？</b></summary>

不会。除首次模型下载走镜像站外，写词、生成、换声、训练、歌词对齐全部在本机完成，无任何遥测。
</details>

<details>
<summary><b>歌词对齐为什么不是"识别"出来的？</b></summary>

老方案用语音识别（ASR）反推时间轴，但歌声上 ASR 漏识率高达 20-30%（实测错得离谱），误差逐级累积。新方案走**强制对齐**路线：歌词文本是已知的，直接把已知歌词"压"到音频上——中文走 FunASR 字级时间戳，英文走 wav2vec2 CTC 对齐，最后用 VAD 人声起音点做锚点吸附消除集体漂移。字/词级误差在百毫秒量级，且支持逐字高亮的卡拉OK效果。
</details>

<details>
<summary><b>音色训练中断了要重来吗？</b></summary>

不用。音色制作支持断点续跑，中断后从上次进度继续；训练前有模型体检（提前发现权重损坏/不匹配），训练过程支持试听，多音色还能融合。
</details>

<details>
<summary><b>能商用吗？</b></summary>

不能。YuE2 模型权重遵循 CC-BY-NC 4.0（非商用），本项目扩展代码同样随上游许可分发。详见下方许可说明。
</details>

## 🙏 致谢

- [m-a-p/YuE2](https://huggingface.co/m-a-p/YuE2-3B) —— 音乐生成模型
- [audio.cpp](https://github.com/leejet/audio.cpp) · [GGUF 量化](https://huggingface.co/ngquocvinh/YuE2-3B-GGUF) —— 本地推理引擎
- [FunASR](https://github.com/modelscope/FunASR) · [torchaudio forced alignment](https://pytorch.org/audio/stable/forced_alignment_tutorial.html) —— 歌词字/词级强制对齐
- RVC / SheetSage2 / SenseVoice —— 换声、乐谱、语音识别

## 📄 许可

本仓库扩展代码随各上游组件许可分发；模型权重遵循 [m-a-p/YuE2](https://huggingface.co/m-a-p/YuE2-3B) 原始许可（CC-BY-NC 4.0，**非商用**）。使用/二次分发前请核对 `checkpoints/` 内许可与 `THIRD_PARTY_NOTICES`。

---

<div align="center">

**如果这个项目帮到了你，请点一个 ⭐ —— 让更多热爱音乐的人看到它！**

[⬆ 回到顶部](#-音乐工作台-yue2)

</div>
