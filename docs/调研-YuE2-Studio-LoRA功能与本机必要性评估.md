# YuE2-Studio 的 LoRA 功能调研 + 本机是否要跟进（2026-09-29）

## 0. 一句话结论

**不要做"训练 LoRA"，这台机器和这个引擎都过不去；但值得留一条"用别人的 LoRA"的后门——前提是先确认上游引擎支持，目前我们的引擎不支持（下面有二进制证据）。**

---

## 1. YuE2-Studio 是谁，跟我们什么关系

- 仓库：`timoncool/YuE2-Studio`（Windows 桌面应用，Tauri = Rust + React，运行时不含 Python）。
- 引擎：`yue2.cpp` / audio.cpp 系的 C++/GGML 原生移植，跟我们是**同一个 YuE2-3B 模型、同一条 GGUF 路线**，只是壳不同。
- 它的功能清单里有我们熟悉的东西：full/melody/off 三档 CoT、SheetSage2 音频转谱、封面翻唱、批量排队、ABC 乐谱编辑、stem 分离、降噪/VST 后期。区别是它多了 MIDI 导出、Winamp 皮肤播放器、MCP 服务端，**以及 LoRA 页面**。
- 所谓"能训练自己的 LoRA"，训练器不是它自己写的：README 明确写的是 **HOT-Step 的训练器**（`scragnog/HOT-Step-CPP`，一个同时跑 ACE-Step 1.5 / MiniMax-Music3 / YuE2 三个后端的 GGML 引擎）。也就是说 LoRA 能力来自另一个开源项目，YuE2-Studio 只是把它接进 UI。

## 2. 在 YuE2 上，LoRA 到底训的是哪两块

YuE2 是两段式：**AR（会写谱的"作曲脑"，28 层 Llama 结构）→ NAR（flow-matching 的"发声体"）→ VAE 出声**。LoRA 分别挂在这两段上，各管一件事：

| 挂哪一段 | 改的是 | 听得出来的效果 |
|---|---|---|
| AR（planner / 作曲） | 结构、旋律习惯、和声语汇、流派走向、段落规划能力 | 歌曲"像不像那个流派"、会不会自己收尾、器乐曲能不能按 `[intro][verse][chorus]` 走 |
| NAR（decoder / 声音） | 音色、混音质感、演唱的贴麦感、乐器质地 | 同一段曲子换一套"制作"，从干瘪 DEMO 感变成品唱片感 |

两段各有一个独立强度旋钮，社区推荐起手 **AR≈0.5 / NAR≈1.0**（AR 拉满容易把曲子带跑）。

四个已发布的真实配方（说明"要多少数据、多少显存"）：

| 谁的 LoRA | 配方 | 训了什么 |
|---|---|---|
| Mothersuperior 器乐 CoT LoRA | AR，rank 64，全部 28 层的 attn+MLP，**约 2700 首器乐曲**配 SheetSage2 标了和弦的 ABC 谱，50% 用 YuE2 自生成数据做正则 | 让模型学会"写纯器乐曲 + 按段落规划"，还提供 `[intro 0:00-0:15]` 这种带时间的段落标签 |
| monsterovich 工业摇滚 | AR rank 8 + NAR rank 32，**179 首**，各 1200 步；AR 约 35MB、NAR 约 140MB | 流派质感；并且公开了一次**训练 bug**：NAR 第一版因为 gradient checkpointing 处把层输入 detach 了，反传链被切断，结果"听着没问题但音色是虚的"，后来重训才修好 |
| HaileyStorm 卧室流行（某歌手向） | rank 32 PEFT，AI Toolkit，第 950 步检查点 | "贴麦气声 + 低频很重"的演唱与混音取向；作者交付方式是整张专辑 8 首 × 3 seed 试听挑选 |
| storagejuju J-POP | **只训 NAR**，rank 16，6 秒切片，2000 步，FP16 底模，**一张 T4（16GB）** | 触发词 `jpstyle26` 写进 style 里 |

HOT-Step 默认配方更轻：LoKr dim64/factor4/alpha256，AR+NAR 一共约 106MB；预置三档 Balanced = 100 次更新 × 每次 4 首。

## 3. 什么情况下真的需要训 LoRA

需求判定可以用一句话卡：**"style 标签 + 谱编辑 + RVC 都试过，还是不到位"，才轮到 LoRA。**

值得训的四种情形：

1. **模型压根没这个流派的语汇**（冷门乐器、地方性律动、戏曲×电子这种混搭），标签怎么写都出不来那个"味道"。
2. **要修行为缺陷，不是要改审美**。典型是 Mothersuperior 那个例子：基础模型写纯器乐曲不可靠、歌容易冲到高潮就散，它用 2700 首把"按段落规划 + 会收尾"训进去了。这类"能力"问题 LoRA 是最直接的解法。
3. **要一个稳定的"厂牌声"**——一批歌共用同一套制作质感，靠逐首歌碰标签碰不齐。
4. **要锁一种演唱质感**（贴麦、气声、失真厚度）。注意这跟"复刻某个人的声纹"是两回事。

不该用 LoRA 的情形（我们已经有更合适的工具）：

- **换歌手音色** → RVC。工作台里 `/rvc/train`（`app.py:5605`）已经是完整流水线，且已装 5 个女声权重。NAR LoRA 学的是"制作质感"，学不像人；想"像孙燕姿"就用 RVC，别训 LoRA。
- **改歌词、改结构、改调、改拍速** → 段落标签 + ABC 谱编辑 + 转调链路，全都零训练成本。
- **降 AI 味** → `mc-ai-tell-audit` / `lw-ai-tell-audit` 那套诊断，LoRA 只会把模型的默认毛病换成你自己的毛病。
- **只是想要"更好的流行歌"** → 先把 style 模板库和 CoT=full 用满。LoRA 不是提质量刑具，是改取向的；训得不够的数据只会让输出变窄、变复读。

## 4. 代价（这是本机的判决点）

**显存**（一手口径，来自 HOT-Step YuE2 训练文档 + YuE2-Studio README）：

- 解码段按"整首歌"训：**约 12GB**；训练器本体约 **14GB**。
- 训练时还要边训边出试听（checkpoint ladder）：**引擎+渲染 10-12GB，实测合计约 22GB**。
- 用耳朵给数据集自动写 caption（MOSS-Music-8B）：**约 12GB + 10GB 磁盘**。
- YuE2-Studio 官方对训练写的门槛是：**RTX 30 系及以上 + 11GB 显存 + 约 8GB 额外磁盘**。

**磁盘**：训练底模（ConvRot int8）3.69GB + 音频转码器 1.13GB + SheetSage2 谱 1.27GB + 歌词对齐 1.18GB + MOSS caption 9.7GB ≈ **17GB 起步**。

**时间**（RTX 5090 上，15 首一张专辑）：Fast 约 25 分钟 / Balanced 约 50 分钟 / Thorough 约 3.5 小时。文档还给了绝对节奏：**4 首歌一次更新，CUDA 约 1 分钟、Vulkan 4-5 分钟（4090）**。

**验收**：只能靠耳朵。文档原话是"没有任何自动指标能分辨好的检查点和已经衰坏的检查点，所以 ladder 就是一轮试听"。训过头的症状是**歌尾部开始循环、咬字散掉**。加上前面那个反传链被切断的 bug——训练这件事本身踩坑率不低。

## 5. 本机为什么过不去（实测，不是推测）

| 项 | 实测值 | 影响 |
|---|---|---|
| GPU | `nvidia-smi`：**GTX 1660 SUPER，6144MB**，当前 54°C/34.5W | 只有官方训练门槛 11GB 的一半；12GB 的"整首歌训 NAR"直接装不下。5090 那套时间要再乘好几倍，Balanced 一轮按天算 |
| CPU 散热 | 已记录故障：i7-10700 约 50% 负载 68W 即撞 100°C 墙 | 数据准备链（分离、对齐、caption、latent cache）是长时间连续负载，机器会撞墙 |
| 生成侧现状 | README:265：full+32 步在 6GB 上 **60-90 分钟/首** | 训练里每个 checkpoint 试听 = 一次生成；ladder 有 10-20 级，光试听就 10-30 小时 |
| **引擎不支持** | 直接扫 `cpp/audiocpp_server.exe` 二进制（223MB）：`lora` 命中的字符串**全是 `vibevoice.lora.*`**（TTS 那条链），YuE2 侧 **123 个 `yue2.*` 键里没有任何 LoRA/adapter 相关键** | 就算在别处训好了 LoRA，我们的引擎今天**读不了** |

再补一层结构性限制：`main.cp312-win_amd64.pyd` 不可改（README:214），`audiocpp_server.exe` 是闭源二进制，我们能动的只有 `app.py` 外圈和 `audiocpp.py` 适配层。**LoRA 是权重加载层的能力，不在我们能改的层里。** 唯一不需要引擎配合的邪路是"离线把 `W += B@A` 合并进 GGUF 再重新量化"——理论上可行，但把浮点 delta 加到 q4_k_m 反量化再量化回去的权重上会不会把声音搞糊，**没有任何人验证过**，我不打算拿这台机器赌。

## 6. 裁定与三条可选路线

**裁定：本工作台不实现 LoRA 训练。** 收益不确定、显存差 2 倍、时间按天算、验收要人肉试听 ladder、而且引擎读不了产物——五个独立门槛，任何一个都能否掉，现在是五个都在。

替代路线，按性价比排：

- **A. 想要 LoRA 的效果，就把它当外挂工具用。** HOT-Step CPP 是独立本地应用（浏览器开 `localhost:3001`，自带训练 Studio），YuE2-Studio 是装机版。要真做某位歌手/某个流派的定制，在**云端租一张 24GB 卡（Colab Pro / 4090 时租）** 用它们的配方训一次，产出的 AR+NAR 适配器（约 106-180MB）先**用它们自己的引擎试听验收**。这一步的意义是"证明 LoRA 对你真的有效"，成本几十块钱一次，而不是在工作台上先修一条 6GB 卡跑不动的流水线。
- **B. 留在我们的线上监听上游。** 只要哪天 audio.cpp / yue2.cpp 的 YuE2 路径出现 `yue2.*lora` 这类键（我用同样方式扫二进制就能确认），**加载社区现成 LoRA** 的成本就极低：`audiocpp.py` 传参 + `server.json` 的 `session_options` 加一项 + UI 一个下拉，不需要训练功能也能吃到 Mothersuperior 那批现成器乐/流派适配器。这是真正值得排的队，且是"跟进"而不是"自建"。
- **C. 现在最该做的还是把不训练的那半边榨干**：style 模板库、CoT=full 的段落标签、RVC 音色库（已装 5 个）、`mc-ai-tell-audit`。这几个的边际收益，按本机现状全都远高于训 LoRA。

## 7. 未验证 / 需要人来判断的部分

- 6GB 卡能不能靠"裁窗 + GGUF Q4 底模 + 关掉试听"硬挤下 YuE2 LoRA 训练：**官方没给数字**，文档里"整首歌训 NAR≈12GB"是唯一口径。我判断不可行，但这是推断，不是实测。
- GGUF 权重离线合并 LoRA 的音质影响：**无人验证**。
- 上面所有时间数字来自 RTX 5090/4090，本机 1660 SUPER 只有倍数级放大的定性判断，**没有实测**。
- 试听验收全部需要你自己耳朵判——按既定分工，听感归你。

## 8. 来源

- [YuE2-Studio（README / 功能与硬件门槛 / mcp-skill）](https://github.com/timoncool/YuE2-Studio#readme)
- [HOT-Step-CPP（Training Studio）](https://github.com/scragnog/HOT-Step-CPP) · [YuE2 training 要求与配方](https://github.com/scragnog/HOT-Step-CPP/blob/master/docs/user/training/yue2.md) · [Adapters](https://github.com/scragnog/HOT-Step-CPP/blob/master/docs/user/adapters.md)
- [scragnog/YuE2-GGUF（ConvRot int8 训练底模）](https://huggingface.co/scragnog/YuE2-GGUF)
- [Mothersuperior/YuE2-instrumental-cot-full-loras](https://huggingface.co/Mothersuperior/YuE2-instrumental-cot-full-loras) · [monsterovich/yue2-industrial-rock-lora](https://huggingface.co/monsterovich/yue2-industrial-rock-lora) · [HaileyStorm/sv-billie-yue2-lora](https://huggingface.co/HaileyStorm/sv-billie-yue2-lora) · [storagejuju/yue2-jpop-t4-lora](https://huggingface.co/storagejuju/yue2-jpop-t4-lora)
- 本机一手证据：`cpp/audiocpp_server.exe` 二进制键名扫描、`cpp/server.json`、`nvidia-smi` 读数、`README.md:140/214/265`、`app.py:5605`
