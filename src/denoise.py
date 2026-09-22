"""GTCRN denoise for reference audio."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
from pathlib import Path

from fastapi import HTTPException

from settings import settings

logger = logging.getLogger("fireredtts3_gateway")

_lock = threading.Lock()
_model = None


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        root = _root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        ckpt_path = Path(settings.gtcrn_ckpt)
        if not ckpt_path.is_absolute():
            ckpt_path = root / ckpt_path
        if not ckpt_path.is_file():
            raise HTTPException(status_code=503, detail=f"GTCRN ckpt not found: {ckpt_path}")
        try:
            import torch
            from scripts.gtcrn import GTCRN
        except ImportError as exc:
            raise HTTPException(status_code=503, detail=f"GTCRN deps missing: {exc}") from exc
        logger.info("loading GTCRN %s device=%s", ckpt_path, settings.gtcrn_device)
        device = torch.device(settings.gtcrn_device)
        model = GTCRN().to(device).eval()
        ckpt = torch.load(str(ckpt_path), map_location=device)
        state = ckpt["model"] if isinstance(ckpt, dict) and "model" in ckpt else ckpt
        model.load_state_dict(state)
        _model = (model, device)
        return _model


def _load_mono_16k(path: str):
    try:
        import soundfile as sf
        import numpy as np
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="soundfile is required") from exc

    wav, fs = sf.read(path, dtype="float32", always_2d=True)
    mix = wav.mean(axis=1)
    if fs != 16000:
        try:
            import librosa

            mix = librosa.resample(y=mix, orig_sr=fs, target_sr=16000)
        except Exception:
            import numpy as np

            duration = len(mix) / float(fs)
            n = int(duration * 16000)
            x_old = np.linspace(0.0, 1.0, num=len(mix), endpoint=False)
            x_new = np.linspace(0.0, 1.0, num=max(n, 1), endpoint=False)
            mix = np.interp(x_new, x_old, mix).astype("float32")
        fs = 16000
    return mix.astype("float32"), fs


def denoise_wav_bytes(data: bytes, filename: str = "ref.wav") -> bytes:
    if not data:
        raise HTTPException(status_code=400, detail="audio is empty")
    import soundfile as sf
    import torch

    model, device = _load_model()
    suffix = Path(filename or "ref.wav").suffix.lower() or ".wav"
    if suffix not in {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".webm"}:
        suffix = ".wav"
    fd, src = tempfile.mkstemp(prefix="fr3_dn_in_", suffix=suffix)
    os.close(fd)
    out = src + ".enh.wav"
    try:
        with open(src, "wb") as fh:
            fh.write(data)
        mix, fs = _load_mono_16k(src)
        window = torch.hann_window(512).pow(0.5).to(device)
        wave = torch.from_numpy(mix).to(device)
        input_stft = torch.stft(
            wave,
            n_fft=512,
            hop_length=256,
            win_length=512,
            window=window,
            return_complex=True,
        )
        input_for_model = torch.view_as_real(input_stft)
        with torch.no_grad():
            output_from_model = model(input_for_model[None])[0]
        output_complex = torch.view_as_complex(output_from_model.contiguous())
        enh = torch.istft(
            output_complex,
            n_fft=512,
            hop_length=256,
            win_length=512,
            window=window,
        )
        pcm = enh.detach().cpu().numpy()
        sf.write(out, pcm, fs)
        return Path(out).read_bytes()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("GTCRN denoise failed")
        raise HTTPException(status_code=500, detail=f"denoise failed: {exc}") from exc
    finally:
        for p in (src, out):
            try:
                os.unlink(p)
            except OSError:
                pass
