"""legacy VR（UVR-*）在 CUDA 上整批吐 NaN→数字静音 的定位工具（诊断用，不是恢复件）。

背景：2026-09-27「蛋卷」事故。本机 cuDNN 9.17 + GTX 1660 SUPER 上，UVR 多 band 卷积
前向里第一处 NaN 在 dec3，掩码全 NaN 后被上游 nan_to_num 洗成 0，产物退出码 0、
文件齐全、内容全数字静音。app.py 的修复是给这类子进程加 PYMSS_DISABLE_CUDNN=1
（由 py312 的 sitecustomize.py 读掉、关 torch.backends.cudnn），本脚本用于复现与复验。

用法（必须从 runtime/rvc 目录跑，那里才有 tools/pymss* 与模型目录）：

    # 复现：不加环境变量 → 应看到 dec3 起 NaN、最终产物峰值 0.0
    py312/python ../../patches/rvc-clean-guards/repro_nan_trace.py 某干声.wav

    # 复验修复：加 PYMSS_DISABLE_CUDNN=1 → 全程 ok、产物峰值 ≈ 0.29（与 CPU 一致）
    set PYMSS_DISABLE_CUDNN=1 && py312/python ...同上...

注意：脚本会把权重缓存写到 CWD（store_dirs='o'）——跑完把 runtime/rvc/o 删掉，
别把临时缓存留进 runtime。"""
import os
import sys

import numpy as np
import torch
from scipy.io import wavfile

if len(sys.argv) < 2:
    sys.exit(__doc__)
wav_path = sys.argv[1]

sys.path.insert(0, os.path.abspath("tools"))
from pymss_core.modules.vocal_remover.uvr_lib_v5.vr_network import nets_new  # noqa: E402


def _chk(name, t):
    """非浮点张量不判；浮点张量含 NaN 时打印并返回 False。"""
    if not isinstance(t, torch.Tensor) or not t.is_floating_point():
        return True
    if bool(torch.isfinite(t).all()):
        return True
    print(name, f"NaN {int(torch.isnan(t).sum())}/{t.numel()}")
    return False


def fwd(self, x):
    """原 BaseNet.forward 的逐层展开版：报出第一个吐 NaN 的层名后原样返回。"""
    e1 = self.enc1(x)
    if not _chk("enc1", e1):
        return e1
    e2 = self.enc2(e1)
    if not _chk("enc2", e2):
        return e2
    e3 = self.enc3(e2)
    if not _chk("enc3", e3):
        return e3
    e4 = self.enc4(e3)
    if not _chk("enc4", e4):
        return e4
    e5 = self.enc5(e4)
    if not _chk("enc5", e5):
        return e5
    b = self.aspp(e5)
    if not _chk("aspp", b):
        return b
    b = self.dec4(b, e4)
    if not _chk("dec4", b):
        return b
    b = self.dec3(b, e3)
    if not _chk("dec3", b):
        return b
    b = self.dec2(b, e2)
    if not _chk("dec2", b):
        return b
    lm = self.lstm_dec2(b)
    if not _chk("lstm_dec2", lm):
        print("lstm_dec2 input was", "nan" if not _chk("input", b) else "ok")
        return torch.nan_to_num(lm)
    b = torch.cat([b, lm], dim=1)
    if not _chk("cat-lstm", b):
        return b
    b = self.dec1(b, e1)
    if not _chk("dec1", b):
        return b
    return b


nets_new.BaseNet.forward = fwd

from pymss.separator import MSSeparator  # noqa: E402

sr, data = wavfile.read(wav_path)
sep = MSSeparator.from_model_name("UVR-DeNoise", device="cuda",
                                  store_dirs="o", download=True)
sep.load_model()
res = sep._separate(data.T.astype(np.float32), pbar=False)
print("cudnn.enabled =", torch.backends.cudnn.enabled)
print({k: float(np.abs(np.asarray(v)).max()) for k, v in res.items()})
