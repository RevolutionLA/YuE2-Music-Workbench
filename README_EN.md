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
| 🎼 | **AI Songwriting** | Style + lyrics → full vocal song (auto verse/chorus arrangement); paste an ABC score and it sings *your* score (melody-only → `melody`, chords → `full`, route auto-paired); reference audio transcribes with or without chords |
| 🤖 | **AI lyric & style assistant** | Built-in DeepSeek-powered helper: describe your idea in plain words, get structured lyrics and style tags |
| 🎤 | **Cover / re-write / re-style** | Drop in a reference clip → lyrics auto-recognized → sing it again with new words or a new style |
| 🎼🔍 | **Song → ABC score** | SheetSage2 transcription: melody-only by default (pairs with `melody`, what covers usually want); tick **提取时带和弦** for a melody + chord score (pairs with `full`). Chords are decoded in both modes, so the tick costs essentially no time |
| 🗣 | **Voice conversion** | RVC voice conversion: turn your voice into any singer; exports include converted vocal / original vocal / instrumental / full song |
| 📝 | **Synced lyrics (LRC + word-level eLRC)** | Forced alignment, not ASR guessing: the known lyric text is pressed onto the audio — FunASR character-level timestamps for Chinese, wav2vec2 CTC for English, snapped to VAD onsets. Downloads as line-level `.lrc` or word-level `.elrc` (karaoke highlighting for eLRC-capable players) |
| 🎨 | **Custom voice training** | Upload dry vocals, train your own RVC voice models |
| 📦 | **Batch queue** | Queue dozens of songs and walk away; one-click resume after interruption, per-item retry, auto-recovery on restart; each submission gets its own queue identity, so counts never blend into yesterday's batch |
| 🗂 | **Task manager** | Status × type dual filters; per-task rename / stop / retry / delete; running tasks pinned on top; **every task carries a globally unique ID** (= its on-disk filename, click to copy) so duplicate names never get confused |
| 🔔 | **Notifications** | Windows toast on completion (with task name); browser favicon shows busy/idle state |
| ⏸ | **Job persistence** | Refresh the page or close the browser — tasks keep running and results are waiting. After a *service restart* queued items resume and finished artifacts stay intact; leftover in-flight items are labelled by what the machine can actually prove: only items that started **before this process began** are marked "interrupted by restart", items whose worker thread never reported back are marked "queue stopped — re-run this item", and the item a live worker still owns is left for that worker to finish. (A 09-26 smoke run caught the old code blaming a restart whose PID never changed.) |
| 🩺 | **Watchdog self-healing** | Gateway + workbench stuck-detection and auto-restart, tolerant of token-auth 401 probes. Before killing anything it fires one 90 s deep probe: if the service answers, it's *slow*, not dead, and the failure counter just resets. Evidence: 09-26 watchdog log shows two kills (21:40, 21:50) that landed **while the user's own batch was computing** — on a 6 GB card saturated by llama-server, `/api/health` legitimately takes tens of seconds. Respawned gateway stdout/stderr now goes to `runtime/data/logs/gateway.{out,err}.log` instead of the bit bucket, so self-healing stops erasing the scene. |
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

> ⚠️ **Being honest up front: `git clone` gives you the workbench *code*, not the runtime.** These are excluded by `.gitignore` (size / upstream licensing) and are required to boot:
>
> | Not committed | Put it at | Where it comes from |
> |---|---|---|
> | `py312/` (Python 3.12 env + all deps) | repo root | Bring your own 3.12 environment — there is **no root `requirements.txt`**; `checkpoints/requirements*.txt` only covers transcription |
> | `main.cp312-win_amd64.pyd` (compiled gateway core) | repo root | Ships with the YuE2 engine distribution; not redistributable here |
> | `cpp/` (`audiocpp_server.exe` + `server.json`) | `cpp/` | Same; GGUF models via `scripts/download_models.py` |
> | `checkpoints/model.safetensors` (SheetSage2/MERT2) | `checkpoints/` | Manual download from `m-a-p/MERT-v2-30s` / `MERT-v2-FullSong` (only needed for transcription) |
> | `runtime/models/` (local ASR/alignment weights, ~1.6GB) | `runtime/models/` | Only needed for word-level lyric alignment |
>
> The "3 steps to sing" path therefore holds for users of the full distribution package; a plain clone needs the five items above. Missing pieces fail with an explicit error, never a silent downgrade. Tracked as finding D1 in `docs/review/RESPONSE-308f833.md`.

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

> 🧱 **What you can actually change:** `app = main.app` — `main` is the compiled `main.cp312-win_amd64.pyd` (no source, not modifiable). This project's routes hang *around* that core (`/api` prefix) and reorder `app.routes` so their own endpoints win. `audiocpp.py` is the engine adapter the core loads at runtime — it is live code, not dead code.

| Directory | Contents |
|---|---|
| `app.py` | Gateway routes: job hosting / batch queue / resume / retry / RVC / lyric alignment / loopback request guard |
| `main.cp312-win_amd64.pyd` | Compiled gateway core (not committed, not modifiable) |
| `audiocpp.py` | Runtime engine adapter used by the compiled core |
| `src/lrc_align.py` | Forced-alignment engine (character/word timestamps → LRC + word-level eLRC) |
| `src/ports.py` | Single source of truth for ports (`ports.json` + env override + fallback) |
| `static/` | Front-end (theme sync, no state loss on refresh) |
| `dsh-plugin/` | Integrated workbench UI plugin |
| `scripts/` | Launch/stop/model download, `yue2workbench://` protocol registration |
| `watchdog.py` | Watchdog (gateway + workbench self-healing) |
| `tests/` | Regression tests, 57 cases (`py312\python.exe -m unittest discover -s tests`) — tmp dirs and read-only endpoints only, never starts a computation |
| `LICENSE` | Layered licensing, incl. the third-party assets shipped here |
| `cpp/` | YuE2 GGUF engine (`audiocpp_server.exe`); engine and models are not committed, models download via `scripts/download_models.py` |

## 🗂 Versioning & releases

Versions follow semver `vMAJOR.MINOR.PATCH`; **the single source of truth is the git annotated tag**
(`git tag -l -n`), and [CHANGELOG.md](CHANGELOG.md) explains what each one contains. Current baseline:

| Tag | Meaning |
| --- | --- |
| `v1.0` | First open-source release (historical starting point, never rewritten) |
| `v1.1.0` | Unique per-task IDs + chord-aware route pairing + three-round adversarial-review fixes, verified through an authorized restart smoke run |
| `v1.1.1` | LAN mode made to work along the **real** path (the dsh proxy rewrites `Host` to loopback; the UI's Origin port is `:3081`) + the watchdog's early-death circuit breaker |

**Release checklist** — skip one and you may only claim "the code changed", not "it's live":

1. `py312\python.exe -m unittest discover -s tests` fully green;
2. **Smoke after restarting the gateway** (a resident process never picks up new logic on its own): ① appending to a finished queue merges into the same `queue_id` and the total only grows; ② stop/cancel flips pending to `cancelled` and the item still computing is **not** written by the poller; ③ oversized input returns 400, cross-site `Origin` returns 403;
3. Both READMEs and `docs/PROMOTION.md` updated for any behaviour change (project rule);
4. `git tag -a vX.Y.Z -m "..."`, then push tags through the proxy: `git -c http.proxy=http://127.0.0.1:7890 push origin --tags`.

## ❓ FAQ

**Is it really offline?** Yes — after the initial model download, everything (inference, RVC, lyric alignment) runs locally. No data leaves your machine.

**No NVIDIA GPU?** It runs CPU-only automatically. Expect slower generation (several minutes per song).

**Commercial use?** YuE2 model weights are CC BY-NC 4.0 (non-commercial); songs you create are yours to use per YuE2's license. This workbench's own code follows the repo license.

**I pasted an ABC score but it sang something else?** A score in the box now always means "sing my score" — you no longer have to line the 规划 CoT dropdown up by hand. Before submitting, the workbench checks the score's shape in *both* directions: a melody-only score goes through `melody` (free accompaniment), a chord-annotated score goes through `full` (melody + harmony), a swapped selection is corrected, and `off` is corrected back onto a score-consuming route (`off` + score is a hard 400 from the engine). An external ABC bypasses the symbolic planner, but `melody` and `full` are different native instructions, and the engine will not rewrite your score for you — `melody` does not strip the `"C"` / `"Am7"` symbols off a score, `full` does not add harmony you did not write. A mismatched route is what makes the result sound nothing like your score. The score box shows live whether the current score carries chords and which route it will take; the toast and the history entry record the route actually used.

The 🎸 extract-from-reference button yields a **melody-only score** by default (pairs with `melody`, what covers usually want). Tick **提取时带和弦 / extract with chords** next to it and you get a **melody + chord score** (pairs with `full`, harmony anchored too). Chords are decoded either way, so ticking it costs essentially no extra time. Honest limitation: a full score's chord symbols must pass SheetSage2's symbol table; an unrecognized chord quality makes the whole ABC export fail — it raises with the reason instead of silently handing you a stale score from a previous run. If that happens, untick and re-submit.

Note: a score anchors the melodic line (and, with `full`, the harmony) — it does not keep the original singer's timbre or arrangement.

**Task names repeat — how do I tell two runs apart?** Every task carries a **globally unique ID** (`20260926_183413_64c31a01`: creation timestamp + 32 random bits, de-duplicated against both disk and memory). Names are yours to reuse; IDs are not. The ID *is* the artifact filename (`runtime/output/<ID>.wav/.json/.lrc`, RVC and training folders, saved scores), so deleting or retrying one run can never hit its namesake. The ID shows on the task manager rows, the voice-conversion list, the training cards and the live progress line — click it to copy.

Batch submissions additionally get a **queue-level ID** representing *one submission*: queue 2 songs and the panel reports those 2, while older finished entries count separately as "previous queue records" instead of inflating this batch's total. Appending to a queue that is *still running* reuses that queue's name and ID — an append is a continuation of the same queue, not a new one.

**How long can my lyrics be?** Caps: style 2,000 characters, lyrics 20,000, ABC score 20,000. Over the cap the request is **rejected with the exact length and limit** — no more silent truncation. Truncation used to chop the tail off, so the audio and the lyric file stopped matching, and nothing on screen told you it happened. In a batch queue each item is validated on its own; one over-long item only rejects that item.

**What does deleting a task actually take with it?** Everything under that task ID: `runtime/output/<ID>` with `.wav/.json/.txt/.lrc/.lrcjob/.elrc`, plus the voice-conversion workspace `runtime/rvc/jobs/<ID>/` (your uploaded source song and the separated stems — previously only the finished wav was removed and these folders piled up into gigabytes of orphans). Queue records and history entries go with it. Only that whitelist of extensions is touched, matched exactly — passing `*` as the ID deletes nothing. A file locked by a player is left in place rather than reported as deleted.

**Can I expose it to my LAN or the internet?** Not recommended, and by default refused: the gateway binds 127.0.0.1 *and* runs a loopback guard — a request whose `Host` is not a loopback address gets 403 (that's the DNS-rebinding case), and state-changing methods (POST/PUT/PATCH/DELETE) carrying a non-local `Origin`/`Referer` get 403 (browser CORS only blocks *reading* the response, not sending the request, so any web page could otherwise POST at 127.0.0.1 to stop jobs or kill the engine). Responses also carry a CSP `frame-ancestors` listing only local origins, so nobody can iframe your workbench and bait a click.

If you set `settings.app_host` to something like `0.0.0.0` (anything non-loopback), **the gateway refuses to start** and explains why. Two reasons: with the guard still requiring a loopback `Host`, an "opened" gateway would answer every LAN request with 403, which is harder to debug than an honest startup failure; and a relaxed check must still be able to tell *your* devices apart from a web page that DNS-rebinding pointed at your internal address — comparing `Origin` against `Host`, two strings both derived from the attacker's hostname, is not a check at all. So opening it takes both variables: `YUE2_ALLOW_LAN=1` (you know what you are doing) plus `YUE2_LAN_HOSTS=192.168.1.7` (comma-separated hostname allowlist — the exact IP or hostname you type in the browser). Opting out without a hostname allowlist still refuses to start. Once open: `Host` must either hit the allowlist **or be loopback** (the dsh workbench's internal proxy always rewrites `Host: 127.0.0.1:7863`, so refusing loopback would 403 your own UI), state-changing requests need an `Origin` whose host is on the allowlist *and* whose port is **either** the gateway or the workbench port (the UI lives on `:3081`), and CSP `frame-ancestors` **and CORS `allow_origins`** admit only those hosts. LAN mode has no per-user isolation and error details still contain local absolute paths and account names, so put an authenticating reverse proxy in front of it. The safer alternative is to keep it on loopback and front it with a reverse proxy that rewrites `Host` back to `127.0.0.1`.

⚠️ One TCP-layer trap (confirmed on review, not a code bug): set `app_host` to `0.0.0.0`, **not** to a specific LAN IP such as `192.168.1.7`. `ui-panel.mjs` hardcodes `LAB_HOST='127.0.0.1'`, so the local workbench always connects to `127.0.0.1:7863` — bind only a LAN IP and nothing listens on loopback, giving `ECONNREFUSED` before any HTTP guard is even reached. `0.0.0.0` serves loopback and LAN at once; the gateway detects the wrong configuration at startup and prints a ⚠️ warning naming the fix. One tightening of the same kind: switching the inference model (`POST /api/models/switch`) used to validate one variable while writing a *different, unvalidated* one into `server.json`, so `../` could walk the path out of the engine's working directory — it now accepts only a bare filename under `model/` and rejects anything carrying a directory component.

**Do the environment variables set by the launcher actually reach the gateway?** Now they do. The first-launch path used to spawn the gateway through WMI `Win32_Process.Create` (to avoid a console window), and a WMI-spawned process inherits the WMI service's environment — *not* the cmd window's — so `HF_ENDPOINT`, `HF_HOME`, `TORCH_HOME`, `FFMPEG_PATH`, `NO_PROXY` and the key loaded from `secrets\local_env.bat` were all inert on first launch, while the watchdog's restart path passed them explicitly: two different configurations depending on who started the process. Both paths now use `Start-Process -WindowStyle Hidden` — still no window, but the environment is inherited down the chain (A/B verified with the same probe child script: WMI could not see the variable, Start-Process could). The API key still travels only through inherited environment; it is never expanded into a command line, which any local process could read.

**Where does the automatic vocal separation come from?** It uses a local GPT-SoVITS install (with the RoFormer / HPs separation models). The default path is the author's machine; set `YUE2_GSV_ROOT` to your own install. Without it the feature says so plainly and everything else keeps working. Device selection follows the backend mode (no hardcoded `cuda`).

**How do I run the tests?** `py312\python.exe -m unittest discover -s tests -v` — 57 cases in under a second. They only touch temp directories and read-only endpoints and never submit a computation, so they are safe to run while a generation is in flight. Coverage: task-ID uniqueness, the delete-cleanup extension whitelist, input length caps at *both* the generation and the storage entries (history records, templates), chord detection and the melody/full pairing (including "an English word in quotes is not a chord"), legacy queue migration, state-file atomic write, concurrent append and the bounded crash-revive guard, **interruption labels that cannot lie** (a live worker's own in-flight item is never written by the poller; only items predating this process may be called "interrupted by restart"), the loopback guard with its LAN hostname allowlist and port match, **the same guard driven through the headers the dsh proxy actually sends**, refusal to start on a wildcard bind, model-switch path validation, the aggregate upload and free-disk gates, the checkup result cache, and **the watchdog's early-death breaker** (a child that dies seconds after being spawned is a misconfiguration, not a hang — three times and it stops spinning its wheels). Every case is labelled with the review ID it locks.

## 🙏 Credits

- [YuE / YuE2 by HKUST & M-A-P](https://github.com/multimodal-art-projection/YuE) — the foundation model powering everything
- [audio.cpp / GGUF quantization](https://github.com/ggml-org/ggml), [RVC](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [FunASR SenseVoice](https://github.com/modelscope/FunASR)

## 📄 License

See [`LICENSE`](LICENSE) for the full layered statement. Short version: model weights follow their upstream licenses (YuE2 / MERT2 = CC BY-NC 4.0, **non-commercial**), and this workbench's own code is distributed under the same terms — do not plan on commercial use of the outputs either. `checkpoints/` keeps its upstream `LICENSE` and `THIRD_PARTY_NOTICES.md`. **Open item, stated plainly:** `scripts/gtcrn.py` is a vendored network implementation that kept no upstream provenance/license header; it needs one before further redistribution.
