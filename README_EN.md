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
| 🗣 | **Voice conversion** | RVC voice conversion: turn your voice into any singer; each voice's retrieval index is claimed by exact name, so a conversion never silently runs on another voice's index; exports include converted vocal / original vocal / instrumental / full song |
| 📝 | **Synced lyrics (LRC + word-level eLRC)** | Forced alignment, not ASR guessing: the known lyric text is pressed onto the audio — FunASR character-level timestamps for Chinese, wav2vec2 CTC for English, snapped to VAD onsets. Downloads as line-level `.lrc` or word-level `.elrc` (karaoke highlighting for eLRC-capable players) |
| 🎨 | **Custom voice training** | Upload dry vocals, train your own RVC voice models: the dataset is checked *before* you commit hours to it (total length, slice estimate, sample rates, silence and clipping), epoch tiers and time estimates come from this machine's measured pace, batch size follows VRAM, **every checkpoint is saved under its own step** so each tier stays individually auditionable and promotable, a finished run listens to itself and reports an **8–16 kHz noisification ("electro/artifact") measurement plus a consonant-onset (articulation) count** against the source material, and you can retrain from scratch at a lower epoch budget with the old checkpoints, model and index archived rather than overwritten |
| 📦 | **Batch queue** | Queue dozens of songs and walk away; one-click resume after interruption, per-item retry, auto-recovery on restart; each submission gets its own queue identity, so counts never blend into yesterday's batch |
| 🗂 | **Task manager** | Status × type dual filters; per-task rename / stop / retry / delete; running tasks pinned on top; **every task carries a globally unique ID** (= its on-disk filename, click to copy) so duplicate names never get confused |
| 🔔 | **Notifications** | Windows toast on completion (with task name); browser favicon shows busy/idle state |
| ⏸ | **Job persistence** | Refresh the page or close the browser — tasks keep running and results are waiting. After a *service restart* queued items resume and finished artifacts stay intact; leftover in-flight items are labelled by what the machine can actually prove: only items that started **before this process began** are marked "interrupted by restart", items whose worker thread never reported back are marked "queue stopped — re-run this item", and the item a live worker still owns is left for that worker to finish. (A 09-26 smoke run caught the old code blaming a restart whose PID never changed.) |
| 🩺 | **Watchdog self-healing** | Gateway + workbench stuck-detection and auto-restart, tolerant of token-auth 401 probes. Before killing anything it fires one 90 s deep probe: if the service answers, it's *slow*, not dead, and the failure counter just resets. Evidence: 09-26 watchdog log shows two kills (21:40, 21:50) that landed **while the user's own batch was computing** — on a 6 GB card saturated by llama-server, `/api/health` legitimately takes tens of seconds. Respawned gateway stdout/stderr now goes to `runtime/data/logs/gateway.{out,err}.log` instead of the bit bucket, so self-healing stops erasing the scene. |
| 🖥 | **VRAM adaptive** | GPU when it fits, CPU fallback when it doesn't, automatic switch-back |

## 🧭 The 13 pages of the workbench

v2.0 split the old single "Create" page — which had four jobs crammed into it — into one page per step,
so "which step am I on" is self-evident. v10.9 added ⑫ Settings and ⑬ About. The sidebar has three groups:

| Group | Page | What this step does |
|:---|:---|:---|
| **Create** | ① Style assembly | Click style recipes and six facets to produce one Style string (no generation, no playback) |
| | ② Lyrics → song | Style + lyrics → song (includes repeat-N and batch queueing) |
| | ③ Score from a reference song | Upload a song → ABC score, editable / analyzable / storable / MIDI export |
| | ④ Score → song | Style + score + lyrics → sing *your* score (route auto-paired with score type) |
| | ⑧ Lyric & style AI workbench | The DeepSeek assistant (embedded iframe); results fill back into ① or ② |
| **Sound** | ⑤ Voice training | Upload material → checkup → train → audition each tier → promote |
| | ⑥ Voice library | Two libraries (YuE2 reference voices, RVC conversion voices) + import + checkup/f0/merge |
| | ⑪ Voice conversion | Pick voice + pitch + params → RVC conversion (the only route that keeps the original melody and arrangement) |
| | ⑦ Vocal / accompaniment separation | Upload a song → vocals / harmony-stripped vocals / accompaniment, downloadable and sendable to ⑤⑪③ |
| **System** | ⑨ Task manager | One view over all five task kinds (generate / convert / train / transcribe / separate) + queues |
| | ⑫ Settings | Service & model verification, model-tier switch, engine mode, accent colour, maintenance (prefer editing files for config) |
| | ⑬ About | System info (ports / paths / disk & VRAM / engine readiness, read-only) + credits, license and honest notes |

**② and ④ share one generation panel; ③ and ④ share one ABC editor** — switching pages just moves the
same DOM node into the target page (`static/index.html: switchTab` / `moveInto`), so there is no second
copy of a form that can drift out of sync. v1.x bookmarks (`#compose` and friends) redirect to the new pages.

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
| `tests/` | Regression tests, 325 cases (`py312\python.exe -m unittest discover -s tests`) — tmp dirs and read-only endpoints only, never starts a computation. v2.0 adds 87 of them in five files: `test_page_contract.py` (page↔panel counts, unique ids, the shared generation panel and shared ABC editor, dsh sidebar tab list), `test_audio_separate.py`, `test_scores_export.py`, `test_rvc_model_import.py`, `test_system_info.py` (incl. "a new backend field without a UI label turns the suite red") |
| `LICENSE` | Layered licensing, incl. the third-party assets shipped here |
| `cpp/` | YuE2 GGUF engine (`audiocpp_server.exe`); engine and models are not committed, models download via `scripts/download_models.py` |

## 🗂 Versioning & releases

**Version rule (from v10 on, date-based)**: `MAJOR.DAY.SEQ` — `MAJOR` is fixed at `10`, the second
field is the **day of month of the release**, and the third is the **release index within that day**
(starting at `0`). Example: first release on 2026-10-09 → `10.9.0`; a second release the same day →
`10.9.1`; the next day → `10.10.0`. `v2.0.0` and earlier used semver (`vMAJOR.MINOR.PATCH`).
**The single source of truth is the git annotated tag** (`git tag -l -n`), and
[CHANGELOG.md](CHANGELOG.md) explains what each one contains. Current baseline:

| Tag | Meaning |
| --- | --- |
| `v1.0` | First open-source release (historical starting point, never rewritten) |
| `v1.1.0` | Unique per-task IDs + chord-aware route pairing + three-round adversarial-review fixes, verified through an authorized restart smoke run |
| `v1.1.1` | LAN mode made to work along the **real** path (the dsh proxy rewrites `Host` to loopback; the UI's Origin port is `:3081`) + the watchdog's early-death circuit breaker |
| `v1.3.0` | RVC best-practice landing + review fixes (J1–J4 / K1) |
| `v1.3.1` | Three guards for the purification chain's NaN→silence incident (cuDNN path disabled, silence check with a CPU re-run, empty-slice diagnostics) |
| `v1.4.0` | LAN exposure: portproxy + a panel auth gate + heavy work off the event loop + two misfire fixes |
| `v1.4.1` | RVC accompaniment follows transposition: non-octave components folded within ±6 semitones, whole octaves kept |
| `v1.7.0` | RVC training keeps real checkpoints + an "electro/artifact" attribution metric + a retrain channel + long-run interruption / watchdog fixes (v1.5.0 and v1.6.0 shipped inside it) |
| `v1.7.1` | Vocal checkup gains a "consonant onsets per second" axis (mumbled diction is a second disease that spectral flatness cannot see) |
| `v2.0.0` | **Workbench split into 11 pages**: ②④ share one generation panel, ③④ share one ABC editor; new ⑦ separation page, ⑩ settings page, ⑥ voice import, ⑨ unified task view, ⑧ result back-fill; 325 tests green |
| `v10.9.0` | **Data-dense redesign (13 pages)**: whole UI moved to the GitHub data-dense palette (de-AI-flavored), module boundaries and component recognizability rebuilt, new ⑬ About page (system info + credits), fixed the ⑤ "only plain filenames under model/" model-switch bug; 394 tests green |
| `v10.9.1` | **Layout & alignment polish**: ⑪ Task Manager unified onto a five-column skeleton (row height collapsed from two values 72/168 to a single 72), ② Style box 540→78px, lyrics box 100→394px, ⑬ system info flattened from 33 rows to 4 grouped two-column blocks, `scrollbar-gutter: stable` removing the 5px jump when switching tabs; plus two real fixes — a `#tab-ailab` magic number that was 26px off, and silent truncation of footer hints; 410 tests green |

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

**What does the cover (RVC) page give me, and why was there hiss over the intro?** A separated conversion writes four artifacts — **成品/full mix** (converted vocal + the separated accompaniment, accompaniment stereo preserved), **dry vocal**, **original vocal**, **accompaniment** — and the workbench now gives you a separate play button for the mix and for the dry vocal on both the cover page and the task manager, so a dry stem is never presented as the finished song again. Two measured facts drive the rest: an RVC model does *not* output silence where there is no vocal (over the first 28 s of a 221 s song, whose separated vocal was digital zero, the conversion kept humming at -26 dB / 279 Hz — that was the "loud noise floor"), so a **silence gate is on by default**, keyed to the envelope of the audio actually fed to RVC and closing those regions to -60 dB (measured -26.8 dB → -86.8 dB over the intro, median per-frame gain change over sung frames 0.0 dB, i.e. it does not eat words); and **`protect` runs opposite to its label** — it is the weight of the *retrieved* features on non-musical frames, so a *smaller* value keeps more of the original consonants and breath (fewer artifacts), while 0.5 means no protection at all. If harmonies or a duet make the high notes buzz, the pitch tracker is following two voices at once: tick **strip harmony (HP5)** so only the lead vocal is converted, and drop 音色检索强度 toward 0.6. Repeated clicks do **not** run in parallel: conversions are a serial FIFO queue, later ones show 「⏳ queued (position N)」 and start when the one ahead finishes, and a not-yet-started entry can be cancelled (one already converting cannot — the RVC subprocess computes start-to-finish, killing it leaves a broken file). The reason is half VRAM and half separation: two clicks used to load two BS-Roformer models at once, and the second job reported "converting" with a progress bar while it was actually blocked on a lock. Separation now also takes `_GPU_SEM`, so a queued cover cannot collide with a running generation — and a job that is next in line yet still blocked on that lock keeps reporting 「等待本机空闲」 (waiting for the machine to free up) instead of stamping itself `running`, so neither the wall clock nor the progress bar counts someone else's generation as its own conversion. The queue lives in the gateway process — queued entries are lost on restart, and since artifacts are written only when a job completes, no half-written files are left behind.

**Which retrieval index does a conversion use, and why did two of my voices sound identical?** RVC's own lookup walks `assets/indices` then `logs` and accepts a candidate whose name merely *contains* the model stem — a substring rule that silently mis-hangs indexes on each other. Measured on this machine: 「王菲」 conversions were running on 「王菲V6」's index, because sorted by filename V6's candidate won. The model files themselves were the same 55.2 MB weight at the same mtime, so the index was the only thing distinguishing them — hence "two voices sound identical". Now the lookup ranks a name that genuinely belongs to the voice first, the conversion command carries an explicit `--index <that file>`, `index_rate=0` is honoured instead of being eaten by a `or default` expression, and submitting a voice with *no* matching index is refused at submit time with both remedies spelled out (drop retrieval to 0, or add the index) instead of failing after a queue wait. Deleting and renaming a voice touch only its own index copies, and the checkup looks in both directories.

**How do I train a voice I would actually use?** The training page now tells you what you are about to spend before you spend it. `POST /api/rvc/train/check` inspects the uploaded material first — total length, estimated slice count, sample rates, silence ratio (RMS-based, not VAD) and clipping — and suggests epoch tiers from it; measured against the real slicer, a 35 s synthetic set reported 9 slices and `preprocess.py` produced 9. Time estimates come only from this machine's measured pace (`_rvc_epoch_rate`); before a run has ever finished an epoch the card says so in words ("no measurement yet, assuming 240 s/epoch conservatively") rather than quoting a formula as fact. `batch_size` follows actual VRAM (upstream's `VRAM_GB // 2` rule) instead of a hardcoded 4, and the card records why the number it picked is what it is. When training ends, the gateway auditions the run on CPU using *your own* training slices (the first slice of the raw upload used to take 139.6 s where a clean slice takes 15.1 s), then measures the 8–16 kHz spectral flatness of the source and of the output **only on frames that are actually sung** and writes the shift onto the card: if the conversion made the vocal measurably noisier, the card says so out loud (⚠) and points at the model or the material rather than at the cover knobs — digital silence has a flat spectrum and reads as fizz, which is exactly the misjudgment this metric had to be corrected for, now locked by a test. The same pair is also measured for **consonant onsets per second in 3–8 kHz**: "mumbly diction" is energy smeared flat, total HF energy moves 0.8 dB and flatness stays green, so only the onset count sees it (below 75% retained the card warns to promote a better-trained tier). The onset gate filters on loudness only — fricatives are unvoiced, so filtering on "voiced" as well removes precisely what is being measured. The loss head/tail comparison uses **medians and skips epoch 1**, because the first `loss_disc` spike (2.9e9 measured on this machine) bent the mean into a fake trend. Training saves with `-l 0`, so every tier lands in its own `G_{step}.pth` (30-epoch smoke run on this machine produced G_60/G_80/G_120); under the previous `-l 1` all 100 epochs were written over one fixed `G_2333333.pth`, so the "audition each checkpoint / promote this step" controls had exactly one file behind them. Pruning now keeps four tiers — final plus roughly 25%, 50% and 75% — because over-training only becomes audible in the middle, and the 25% tier went in the same day as the measurement that proved it: auditioning 30/42/60 epochs of the same material produced flatness 0.667 → 0.785 → no measurable sung frames at all, i.e. the more it trained the dirtier it got, so keeping only the upper half would have deleted the one tier that might be clean; the dropdown labels them as epochs instead of raw steps. Retraining at a smaller epoch budget needs no re-upload: `POST /api/rvc/train/resume/{rid}` with `epochs=` and `restart=yes` moves the old checkpoints *and* `train.log`/tfevents into `logs/<voice>/ckpt_archive/` (moved, not deleted — otherwise the card reports "epoch 100 of 60" and the liveness check is held up by the previous run's timestamps) and copies the finished `.pth` plus its `.index` into `before_rerun/` before overwriting them; a plain resume without `restart` still may only raise the epoch count. Nothing claims `running` while it is still waiting on the GPU lock. Honest limits: the self-check runs inside the training job's lock, silence is RMS not VAD, pace is only trustworthy per machine, one experiment directory can only use one save scheme (mixing per-step files with the legacy in-place archive makes `latest_checkpoint_path` pick 2333333 over every real step), and voices trained before this change have no per-step checkpoints left to audition.

**How do I run the tests?** `py312\python.exe -m unittest discover -s tests -v` — 325 cases in about 50 s on this machine. They only touch temp directories and read-only endpoints and never submit a computation, so they are safe to run while a generation is in flight. Coverage: task-ID uniqueness, the delete-cleanup extension whitelist, input length caps at *both* the generation and the storage entries (history records, templates), chord detection and the melody/full pairing (including "an English word in quotes is not a chord"), legacy queue migration, state-file atomic write, concurrent append and the bounded crash-revive guard, **interruption labels that cannot lie** (a live worker's own in-flight item is never written by the poller; only items predating this process may be called "interrupted by restart"), the loopback guard with its LAN hostname allowlist and port match, **the same guard driven through the headers the dsh proxy actually sends**, refusal to start on a wildcard bind, model-switch path validation, the aggregate upload and free-disk gates, the checkup result cache, and **the watchdog's early-death breaker** (a child that dies seconds after being spawned is a misconfiguration, not a hang — three times and it stops spinning its wheels), **the cover silence gate in both directions** (a silent reference must be muted; a reference with vocals in it must come out bit-for-bit as loud as it went in) **and the rule that a stereo accompaniment is never collapsed to mono**, and the **serial cover queue** (three submissions must peak at exactly one job running and execute in submit order; a queued position recomputes as the queue drains; only a not-yet-started entry can be cancelled; the upload entry point must enqueue rather than spawn a thread; queued entries must be visible through `/rvc/active` and `/history/active`, and while a test holds the real `_GPU_SEM` the worker must stay `pending` rather than stamp itself `running` and spawn an inference process). The training side adds more on top of that: **batch size follows real VRAM** (6 GB→3, 16 GB→8, CPU and unknown→4 and must say why), **per-epoch time may only come from a measured pace** (with no measurement yet it must be labelled conservative, and a failed job must never set the pace), **the dataset checkup reports length, silence and clipping and its dataset is reused by the training call instead of re-uploading**, **epoch tiers can go below the old floor**, **pruning keeps the final tier plus the ~25%, ~50% and ~75% tiers with G/D staying paired**, **one directory sticks to one save scheme** (a legacy directory holding only `G_2333333.pth` keeps using `-l 1` instead of silently mixing), **a restart archives every checkpoint instead of deleting any**, **the tier label names the epoch rather than the step**, **shrinking the epoch budget without `restart=yes` is a 400 that explains the way out**, **the loss summary survives a cold-start and single-batch spikes**, **each checkpoint auditions from its own cached small model**, **a stale export cannot masquerade as this run's result**, **the audition source prefers the training-side clean slices**, and **the three promote failure paths (400/404/500)** — the 400 now lists the checkpoints that actually exist instead of quoting a filename this machine no longer produces. A retrain also wipes the *previous* run's verdicts off this run's card — self-check result, loss summary, checkpoint list and the audition sample (moved to `before_rerun/prev_preview.wav`, never deleted) — because while epoch 6 of a fresh 60-epoch run was in flight the card was still showing yesterday's 100-epoch conclusion and a checkpoint name that had already been archived. A checked-but-never-trained dataset is now listed by `/rvc/train/active` and deletable from the page: `DELETE /api/rvc/train/{rid}` existed in the API with **no caller in the UI at all**, so several hundred MB of uploaded material nobody could see could never be removed. The noisification metric adds 5 more: a digitally-silent stretch must never be counted as fizz, the noisier of two renditions of the same melody must lose on every axis, `_rvc_quality_report` has to blame the right layer, an output that yields *no* measurable sung frames while the source does must be reported as the worst verdict rather than as missing data (the 60-epoch audition was saturated end to end with no stable pitch, and the card showed only "self-check clip generated"), and the consonant-onset counter has to tell a hard attack from the same energy with a 0.35 s soft attack — fricatives are unvoiced, so the onset gate filters on loudness only; filtering on "voiced" too deletes exactly what is being measured (that false negative made the 5-epoch tier look fine at 0.88 when it is really 0.66). The watchdog adds 7 locks around **"do not kill a training run that is still making progress"**: the pardon only applies when something is actually listening on the port *and* a long job wrote a file recently — a dead port with warm job files must still be respawned, which is exactly the hole measured at 02:56 when the guard abstained and the page stayed unreachable. On the cover side, **the real worker must pass `--index` pointing at that voice's own index** and omit the flag when retrieval is off — the regression lock for the cross-voice mix-up described above. The cover-parameter gates are locked too: an even smoothing radius is clamped to the next odd number (scipy medfilt rejects even kernels), submitting fcpe on a runtime without CLI support or without the torchfcpe dependency fails fast with an actionable 400, and a worker that detects an unpatched CLI omits --filter-radius and records a caps_warn rather than crashing or degrading silently; the capability probe itself parses --help, caches, and degrades to "no capabilities" on probe failure. Its fakes stub the real call path, so they never touch the GPU. The dataset-purification gates (review A1) add 13 more: the two clean stages must really shell out via `-m pymss.cli` (the module-alias crash is a real-machine blocker for VR-architecture models), clean stems are matched by exact normalised name, never substring ("No Noise" contains "Noise"), a missing output falls back to a per-file retry and is counted, a whole-batch failure is retried per file, and an all-fail run raises *before* any file is moved into the purified directory (so a resume can never mistake an uncleaned copy for finished work), a resume skips an already-purified set, a short stem cannot steal a longer stem's output by prefix (`s` must not grab `s0_Dry.wav`), every tier model must exist in the vendored catalog, the submit gate accepts only off/light/medium and the tier is inherited on resume, and the checkup's noise floor recommends medium/light/no-purify at -38.8/-46/very-clean respectively while admitting honestly that reverb cannot be measured. Pitch-filing and cleanup (review J1 plus our own K1 and J3) add 9 more: a downloaded voice filing its own reference audio unlocks the transposition suggestion and must be labelled as coming from the reference, never passed off as a training measurement; filing by reference on a voice that already has `2a_f0` is a 409 that writes nothing (the training set is that voice's first-hand evidence and a reference may not override it), with the reference used only when `2a_f0` is absent or empty; an unknown voice is a 404 and pure silence a 422 that leaves no fake archive behind. Deleting a cover record must also take the three artefacts it registered (`<id>_full_song.wav` and friends — 30–60 MB each, previously orphaned on every delete), while a traversal name or another task's filename planted in its meta stays untouched. A purification child that exits 0 having produced nothing is not a success. Every case is labelled with the review ID it locks.

## 🙏 Credits

- [YuE / YuE2 by HKUST & M-A-P](https://github.com/multimodal-art-projection/YuE) — the foundation model powering everything
- [audio.cpp / GGUF quantization](https://github.com/ggml-org/ggml), [RVC](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [FunASR SenseVoice](https://github.com/modelscope/FunASR)

## 📄 License

See [`LICENSE`](LICENSE) for the full layered statement. Short version: model weights follow their upstream licenses (YuE2 / MERT2 = CC BY-NC 4.0, **non-commercial**), and this workbench's own code is distributed under the same terms — do not plan on commercial use of the outputs either. `checkpoints/` keeps its upstream `LICENSE` and `THIRD_PARTY_NOTICES.md`. **Open item, stated plainly:** `scripts/gtcrn.py` is a vendored network implementation that kept no upstream provenance/license header; it needs one before further redistribution.
