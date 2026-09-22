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
    if isinstance(result, dict):
        abc = str(result.get("abc") or "").strip()
    abc_file = out_dir / "score.abc"
    if abc:
        abc_file.write_text(abc, encoding="utf-8")
        return abc
    if abc_file.is_file():
        return abc_file.read_text(encoding="utf-8").strip()
    raise RuntimeError("官方转谱没有返回乐谱")
