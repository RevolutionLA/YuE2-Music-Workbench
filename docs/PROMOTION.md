# 宣传文案模板（各社区直接复制使用）

> 使用提示：发帖前把「【】」占位符替换成实际内容；最好配 1-2 张截图或 30 秒试听片段，转化率会高很多。
>
> 版本号只从 `CHANGELOG.md` 顶部条目取（真源是 git 附注标签，`git tag -l -n` 可查），不要把版本
> 号写死进文案——文案里留着的数字会在下一次发版后变成假信息。

---

## 一、Reddit r/LocalLLaMA（英文，本地部署主力社区）

**标题：**
I built a fully local AI music workstation on YuE2 — songwriting, AI covers, RVC voice conversion & synced lyrics, 100% offline on Windows

**正文：**
I've been working on this for a while and it's finally open-source: **YuE2 Music Workbench** — a local music workstation built on top of the YuE2 (M-A-P/HKUST) foundation model, running GGUF-quantized inference via audio.cpp.

**What it does, all offline:**
- 🎼 Style + lyrics → full vocal song (auto verse/chorus arrangement), melody editing via ABC notation
- 🎤 AI covers: drop a reference clip → lyrics auto-recognized → re-sing with new words/style
- 🗣 RVC voice conversion + custom voice training
- 📝 Auto synced-lyrics: **forced alignment** (known lyric text pressed onto the audio) → line-level `.lrc` + word-level `.elrc` for karaoke highlighting
- 📦 Batch queue (queue dozens, walk away), task manager, watchdog self-healing
- 🖥 VRAM-adaptive: GPU when it fits, CPU fallback (no NVIDIA GPU needed, just slower)

Repo: https://github.com/RevolutionLA/YuE2-Music-Workbench

Requirements: Windows x64, 16GB+ RAM, 6GB+ VRAM recommended (CPU-only works). First run auto-downloads the ~2.7GB GGUF model.

Happy to answer questions about the audio.cpp quantized inference setup or the lyrics alignment pipeline.

---

## 二、Reddit r/SunoAI 或 r/AImusic（英文，强调免费替代）

**标题：**
Tired of subscription credits? I made a free, offline "Suno alternative" — full songs from lyrics on your own PC (YuE2-based, open source)

**正文：**
If you're tired of burning credits and uploading your lyrics to the cloud: this runs entirely on your own machine. Write lyrics → get a full song with vocals + accompaniment, plus AI covers, voice conversion, and synced .lrc lyrics you can import into any player.

- No account, no credits, no uploads — works fully offline after model download
- Windows one-click launcher, models auto-download (~2.7GB)
- Open source: https://github.com/RevolutionLA/YuE2-Music-Workbench

【附 1 段 30 秒试听或截图】

---

## 三、YuE 官方 GitHub Discussions（英文，最精准流量）

**标题：**
YuE2 Music Workbench — a full workstation GUI for YuE2 (GGUF local inference, covers, RVC, batch queue, LRC alignment)

**正文：**
Hi! Big fan of YuE/YuE2. I built an end-to-end workstation around it and wanted to share:

**YuE2 Music Workbench** — https://github.com/RevolutionLA/YuE2-Music-Workbench

Key pieces that might interest this community:
- **GGUF quantized local inference** of YuE2-3B via audio.cpp (CUDA + CPU fallback), ~2.7GB q4_k_m
- **Full pipeline**: songwriting → AI covers (SenseVoice ASR transcript of the reference clip) → SheetSage2 score extraction (melody-only, or melody + chords) → RVC voice conversion → **auto synced lyrics** by forced alignment (FunASR character-level timestamps for Chinese, wav2vec2 CTC for English, anchored to VAD onsets)
- **Production features**: batch queue with resume/retry, watchdog self-healing, VRAM-adaptive switching

It's aimed at non-technical users (one-click Windows launcher) but the alignment pipeline details are in `src/lrc_align.py` if anyone wants to reuse it.

Feedback and PRs welcome — and thank you for open-sourcing YuE! 🙏

---

## 四、YuE 官方 Discord（短消息版，音乐/AI 频道）

> Hey everyone! I built a full workstation GUI around YuE2 — local GGUF inference, AI covers, RVC voice conversion, batch queue, and auto-synced LRC lyrics, all offline on Windows. Repo: https://github.com/RevolutionLA/YuE2-Music-Workbench — feedback welcome! 🎵

---

## 五、V2EX 分享创造节点（中文）

**标题：**
开源了一个本地 AI 音乐工作站：写歌、AI 翻唱、RVC 换声、滚动歌词，全程离线

**正文：**
折腾了几个月，把自己用的 AI 音乐工具链整合成了一个开箱即用的工作站，并已开源（当前版本见仓库 `CHANGELOG.md` 顶部条目）：

仓库：https://github.com/RevolutionLA/YuE2-Music-Workbench

**能做什么（100% 本地，不上传）：**
- 写歌：填风格+歌词 → 完整人声歌曲（基于 YuE2 大模型，GGUF 量化推理）
- AI 翻唱：丢一段参考音频，自动识别歌词，换词换曲风重新演绎
- RVC 换声 + 音色制作：把自己的音色变成任意歌手
- 歌曲生成完自动产出滚动歌词：走**强制对齐**（已知歌词压到音频上，中文 FunASR 字级时间戳、英文 wav2vec2 CTC，再用 VAD 起音点吸附），行级 `.lrc` + 逐字 `.elrc` 各一份，直接导入音乐 App
- 批量队列：一次排几十首挂机；任务管理、看门狗自愈、断电续跑

**技术栈**：YuE2-3B GGUF（audio.cpp）+ FastAPI 网关 + RVC + FunASR/wav2vec2 强制对齐，Windows 一键启动，模型自动下载（约 2.7GB，支持 hf-mirror 镜像直连）。

**要求**：Windows x64，16G+ 内存，建议 6G+ 显存（无独显自动走 CPU，慢一些）。

欢迎试用、提 issue / PR。附【截图 / 试听】。

---

## 六、B 站视频脚本框架（3 分钟）

1. **0:00-0:20 钩子**：先放一段 AI 生成成品（30 秒精华），字幕「这首歌从写词到成品 5 分钟，全程我自己的电脑，没花一分钱」
2. **0:20-1:00 现场写一首**：填风格、贴歌词、点生成，展示界面
3. **1:00-1:50 翻唱+换声**：丢参考音频自动识别，换自己音色
4. **1:50-2:20 滚动歌词**：生成完自动出 .lrc，导入播放器逐行滚动
5. **2:20-3:00 收尾**：批量队列挂机画面 + 仓库地址 + 一键启动演示，评论区置顶仓库链接

**标题备选**：
- 《本地免费版 Suno？开源 AI 音乐工作站实测：写歌翻唱换声一条龙》
- 《我把 Suno 搬回了家：YuE2 本地音乐工作站，离线生成不花一分钱》

---

## 七、其他渠道速用一句话

- **Hacker News**（Show HN）：`Show HN: YuE2 Music Workbench – offline AI music studio (songwriting, covers, RVC, LRC) built on GGUF inference`
- **微博/即刻**：开源了一个本地 AI 音乐工作站🎵 写歌+翻唱+换声+滚动歌词，全程离线不花钱，Windows 一键启动。基于 YuE2 大模型，附仓库【链接】
- **酷安/小红书**：侧重截图九宫格 + 「不花钱的 AI 写歌神器，电脑就能跑」

---

## 发布节奏建议

1. 第一周：YuE 官方 Discussions + Discord（精准受众，积累第一批 star）
2. 第二周：Reddit r/LocalLLaMA（周二~周四上午美区时间发帖效果最好）
3. 第三周：V2EX + B 站视频（中文圈）
4. 有 demo 音频后随时发 r/AImusic
5. 每次大版本更新，回去各帖评论区补一条更新留言（旧帖回流）
