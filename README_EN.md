<div align="center">

# 🎵 YuE2 Music Workbench

**A full-stack local AI music workstation built on YuE2 — write, sing, cover, and convert vocals, all on your own PC.**

_No cloud. No queue. No subscription. 100% offline._

[![License](https://img.shields.io/badge/License-CC--BY--NC--4.0-red.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20x64-0078D6.svg?logo=windows11&logoColor=white)](#-quick-start)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![CUDA](https://img.shields.io/badge/CUDA-GPU%2FCPU%20adaptive-76B900.svg?logo=nvidia&logoColor=white)](#-faq)
[![Offline](https://img.shields.io/badge/100%25-Offline-success.svg?logo=shield&logoColor=white)](#-faq)
[![Stars](https://img.shields.io/github/stars/RevolutionLA/YuE2-Music-Workbench?style=social)](https://github.com/RevolutionLA/YuE2-Music-Workbench/stargazers)

**`YuE2`** · **`GGUF quantized`** · **`AI singing`** · **`RVC voice conversion`** · **`LRC lyrics`** · **`local Suno alternative`**

[中文说明](README.md) · [Quick Start](#-quick-start) · [Features](#-features) · [FAQ](#-faq)

⭐ **Star this repo if you find it useful — it means a lot to a solo developer!**

</div>

---

## ✨ Features

| | Feature | Description |
|---|---|---|
| 🎼 | **AI Songwriting** | Style + lyrics → full vocal song (auto verse/chorus arrangement); edit melodies via ABC notation |
| 🤖 | **AI lyric & style assistant** | Built-in DeepSeek-powered helper: describe your idea in plain words, get structured lyrics and style tags |
| 🎤 | **Cover / re-write / re-style** | Drop in a reference clip → lyrics auto-recognized → sing it again with new words or a new style |
| 🗣 | **Voice conversion** | RVC voice conversion: turn your voice into any singer; exports include converted vocal / original vocal / instrumental / full song |
| 📝 | **LRC synced lyrics** | Auto-generated clean `.lrc` per song (whisper/SenseVoice alignment, timestamps only — no metadata headers), import straight into any music player |
| 🎨 | **Custom voice training** | Upload dry vocals, train your own RVC voice models |
| 📦 | **Batch queue** | Queue dozens of songs and walk away; one-click resume after interruption, per-item retry, auto-recovery on restart |
| 🗂 | **Task manager** | Status × type dual filters; per-task rename / stop / retry / delete; running tasks pinned on top |
| 🔔 | **Notifications** | Windows toast on completion (with task name); browser favicon shows busy/idle state |
| ⏸ | **Job persistence** | Refresh the page or close the browser — tasks keep running and results are waiting |
| 🩺 | **Watchdog self-healing** | Stuck gateway/workbench auto-restart; zombie batch entries auto-reset |
| 🖥 | **VRAM adaptive** | GPU when it fits, CPU fallback when it doesn't, automatic switch-back |

## 🚀 Quick Start

> **Windows x64 / 16GB+ RAM / 6GB+ VRAM recommended** (CPU-only works too — just slower)

```bat
:: 1. Clone the repo
git clone https://github.com/RevolutionLA/YuE2-Music-Workbench.git

:: 2. Double-click 启动音乐工作台.bat (root or scripts/)

:: 3. Browser opens the workbench → fill style + lyrics → Generate
```

**First run downloads the model automatically** — YuE2-3B GGUF + VAE (~2.7GB) from HuggingFace (China mainland users get hf-mirror.com mirror by default, resumable). Interrupted? Just run the launcher again.

## 🏗 Architecture

```
Browser ── 3081 integrated workbench (dsh-plugin)
              │ /lab-api/* reverse proxy
              ▼
        7863 FastAPI gateway (app.py: job hosting / batch queue / RVC / LRC align)
              │
              ▼
        8080 audio.cpp inference engine (YuE2 GGUF, CUDA/CPU adaptive)
```

| Directory | Contents |
|---|---|
| `app.py` | Gateway routes: job hosting / batch queue / resume / retry / RVC / lyric alignment |
| `src/lrc.py` | LRC generation (SenseVoice+VAD primary, whisper segment-anchor fallback) |
| `static/` | Front-end (theme sync, no state loss on refresh) |
| `dsh-plugin/` | Integrated workbench UI plugin |
| `scripts/` | Launch/stop/model download, `yue2workbench://` protocol registration |
| `watchdog.py` | Watchdog (gateway + workbench self-healing) |
| `cpp/` | YuE2 GGUF engine (models downloaded automatically, not committed) |

## ❓ FAQ

**Is it really offline?** Yes — after the initial model download, everything (inference, RVC, lyric alignment) runs locally. No data leaves your machine.

**No NVIDIA GPU?** It runs CPU-only automatically. Expect slower generation (several minutes per song).

**Commercial use?** YuE2 model weights are CC BY-NC 4.0 (non-commercial); songs you create are yours to use per YuE2's license. This workbench's own code follows the repo license.

## 🙏 Credits

- [YuE / YuE2 by HKUST & M-A-P](https://github.com/multimodal-art-projection/YuE) — the foundation model powering everything
- [audio.cpp / GGUF quantization](https://github.com/ggml-org/ggml), [RVC](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [FunASR SenseVoice](https://github.com/modelscope/FunASR)

## 📄 License

This workbench: CC BY-NC 4.0. Model weights follow their upstream licenses (see Credits).
