# 开发规矩

## app.py 只许变小（止损线，issue #10/#11）

`app.py` 是网关主文件，单文件已经接近 50 万字节。从 v10.27.1 起定下预算：**只许变小**。

- 新路由 → `src/routers/`（现有 7 个领域模块可参照）
- 新业务逻辑 → 新建 `src/services/` 或 `src/<领域>.py`
- 静态资源引用、页面文案、模板拼接 → 一律不进 `app.py`
- 确实必须在 `app.py` 里长大（进程级队列、引擎句柄、启动顺序这类），
  就在**同一笔提交**里调高 `tests/test_app_budget.py` 的 `BUDGETS["app_py_bytes"]`，
  并在提交信息里写清楚为什么不能进 `src/`
- 每次把一块逻辑拆出去，顺手把 `BUDGETS` 收紧到新的实际值

预算按 **LF 口径**记字节（`scripts/app_size_report.py` 负责归一化）：仓库里存 LF、
Windows 检出成 CRLF，直接 `getsize` 会大近 1 万字节，同一条预算在两台机器上就不成立。

```bash
python -m pytest tests/test_app_budget.py -q     # 门：超预算即红，信息里带增长字节数
python scripts/app_size_report.py                # 报表：当前值 + 最近几笔改动 app.py 的提交的增量
```

CI 的 `lint` job 会跑这个门（`.github/workflows/ci.yml`），超预算直接挡下。

## 前端改动

只改 `web/`（`index.html` / `app.js` / `app.css`），然后 `python scripts/build_ui.py`
重建 `static/index.html`；CI 的 `--check` 会拦"只改生成物、没改源文件"。
