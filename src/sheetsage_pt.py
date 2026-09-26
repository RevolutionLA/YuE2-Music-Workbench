"""Official PyTorch SheetSage2 used by /api/score.

SheetSage2 weights: <project>/checkpoints
Hub downloads (MERT-v2-FullSong etc.): <project>/runtime/hf_download
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger("yue2_gateway")

_model: Any = None
_device: str = "cpu"


def hf_download_dir() -> Path:
    return (ROOT / "runtime" / "hf_download").resolve()


def configure_hf_home() -> Path:
    dest = hf_download_dir()
    dest.mkdir(parents=True, exist_ok=True)
    hub = dest / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(dest)
    os.environ["HF_HUB_CACHE"] = str(hub)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(hub)
    os.environ["TRANSFORMERS_CACHE"] = str(dest / "transformers")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return dest


def mert_cached() -> bool:
    hub = hf_download_dir() / "hub"
    patterns = ("models--*MERT*", "models--m-a-p--MERT-v2-FullSong")
    for pattern in patterns:
        for path in hub.glob(pattern):
            if any(path.rglob("*.safetensors")) or any(path.rglob("*.bin")):
                return True
    return False


def use_offline() -> bool:
    flag = os.environ.get("YUE2_HF_ONLINE", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return False
    flag = os.environ.get("YUE2_HF_OFFLINE", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    return mert_cached()


def apply_offline_mode(offline: bool) -> None:
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)


def resolve_checkpoint() -> Path:
    names = [
        ROOT / "checkpoints",
        ROOT / "checkpoints" / "SheetSage2",
        ROOT / "SheetSage2",
    ]
    for path in names:
        if not path.is_dir():
            continue
        markers = (
            path / "config.json",
            path / "configuration_sheetsage2.py",
            path / "modeling_sheetsage2.py",
        )
        if any(item.is_file() for item in markers) or any(path.glob("*.safetensors")):
            return path
    raise FileNotFoundError(
        "未找到 SheetSage2 权重，请放到项目 checkpoints 目录（需含 config.json）"
    )


def get_model() -> Any:
    global _model, _device
    if _model is not None:
        return _model

    configure_hf_home()
    ckpt = resolve_checkpoint()
    offline = use_offline()
    apply_offline_mode(offline)
    logger.info(
        "sheetsage2 checkpoint=%s hf_home=%s offline=%s mert_cached=%s",
        ckpt,
        hf_download_dir(),
        offline,
        mert_cached(),
    )

    import torch
    from transformers import AutoModel

    _device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        model = AutoModel.from_pretrained(
            str(ckpt),
            trust_remote_code=True,
            local_files_only=offline,
        )
    except Exception:
        if offline:
            raise RuntimeError(
                "本地缓存不完整。请联网一次让 MERT-v2-FullSong 下到项目 hf_download，"
                "或把已有缓存拷进 hf_download/hub"
            ) from None
        raise
    _model = model.eval().to(_device)
    return _model


def unload() -> None:
    """释放 SheetSage2 模型占用的 GPU 显存（生成歌曲前调用，避免挤占生成引擎）。"""
    global _model
    if _model is None:
        return
    try:
        import gc
        import torch
        if _device == "cuda":
            _model.cpu()
            torch.cuda.empty_cache()
    except Exception:
        pass
    _model = None
    gc.collect()


def transcribe_abc(audio_path: str, *, melody_only: bool = True) -> str:
    """音频 → ABC。melody_only=True 只要旋律声部；False 连和弦记号一起写进谱面。

    和弦在两种模式下都会被解码（chord.lab 总是落盘），melody_only 只决定 ABC 里要不要
    写和弦记号，所以 False 几乎不额外花时间。代价是完整谱更容易整次失败：记号要过
    chord_symbol_to_abc，遇到不认的和弦性质就抛错、整份 ABC 变空。
    """
    configure_hf_home()
    model = get_model()
    out_dir = ROOT / "sheetsage2-output"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = model.transcribe(
        str(audio_path),
        output_dir=str(out_dir),
        melody_only=melody_only,
    )
    abc = ""
    reason = ""
    if isinstance(result, dict):
        abc = str(result.get("abc") or "").strip()
        reason = str(result.get("abc_error") or "")
    if not abc:
        # 绝不回读 score.abc：那是上一次转谱留下的旧谱，静默返回会让用户拿到别的歌的谱
        raise RuntimeError("转谱未产出乐谱：" + (reason[:200] or "官方转谱没有返回乐谱"))
    (out_dir / "score.abc").write_text(abc, encoding="utf-8")
    return abc
