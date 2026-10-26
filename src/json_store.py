"""JSON 原子写：先写 .tmp 再 os.replace，崩在中途也不会把原文件截成半截。

用于历史库、乐谱库、模板库这类“一次写整库”的文件：它们不像 output meta 那样
有条目级备份，直接覆盖写一旦断电/异常，整份库都会变成非法 JSON。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any, *, indent: int | None = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 先序列化：不可序列化对象在碰磁盘前就失败，旧文件不受影响
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
