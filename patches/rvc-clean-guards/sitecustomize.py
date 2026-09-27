"""Filter noisy banner lines from stdout/stderr."""
import codecs
import io
import os
import sys

_WATERMARK = "\u5218\u60a6"


class _FilterStream(io.TextIOBase):
    def __init__(self, raw):
        super().__init__()
        self.raw = raw
        self._buf = ""

    @property
    def encoding(self):
        return getattr(self.raw, "encoding", None) or "utf-8"

    def write(self, s):
        if not s:
            return 0
        self._buf += s
        if "\n" not in self._buf and "\r" not in self._buf and _WATERMARK not in self._buf:
            self.raw.write(self._buf)
            self._buf = ""
            return len(s)
        lines = self._buf.splitlines(keepends=True)
        self._buf = ""
        for line in lines:
            if _WATERMARK in line:
                continue
            self.raw.write(line)
        return len(s)

    def flush(self):
        if self._buf and _WATERMARK not in self._buf:
            self.raw.write(self._buf)
        self._buf = ""
        if hasattr(self.raw, "flush"):
            self.raw.flush()

    def fileno(self):
        return self.raw.fileno()

    def isatty(self):
        return self.raw.isatty()

    def __getattr__(self, name):
        return getattr(self.raw, name)


def _install():
    try:
        for name in ("stdout", "stderr"):
            stream = getattr(sys, name, None)
            if stream is None or isinstance(stream, _FilterStream):
                continue
            setattr(sys, name, _FilterStream(stream))
    except Exception:
        pass


def _install_cudnn_off():
    """YOLO-Voice-VR (UVR-* 多 band 卷积) 在 cuDNN 9.17 + GTX 1660 SUPER (sm_75) 上
    整张掩码输出 NaN——NaN 被上游 nan_to_num 洗成 0，净化产物变成数字静音。
    torch.backends.cudnn.enabled=False 绕开：峰值与 CPU 完全一致（0.2962），且更快
    （20s 素材 50.4s → 11.3s；坏掉的 cuDNN 算法连速度都拖垮）。
    只在 PYMSS_DISABLE_CUDNN=1 时生效，其他一切进程不受影响；
    触发方见 app.py 的 _pymss_env_cudnn_off()（只喂 legacy VR 净化子进程）。"""
    if os.environ.get("PYMSS_DISABLE_CUDNN") != "1":
        return
    try:
        import torch.backends.cudnn as _cudnn
        _cudnn.enabled = False
    except Exception:
        pass


_install()
_install_cudnn_off()
