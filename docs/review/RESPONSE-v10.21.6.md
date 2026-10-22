# 开发团队回应 · v10.21.6

> 降级模式：本轮三方评审由主代理串行扮演；详细限制见三份报告开头。

## 已采纳并修复

| 编号 | 处理 | 提交 |
|---|---|---|
| B1 请求刚发出误画 NAR | 改为 `nar_at >= 0` 才进 NAR；新增 queued 回归 | `35bc330` |
| B2 非法 seed 500 | `int(seed)` 包 try/except，不匹配返回 None；新增回归 | `35bc330` |
| B3 通知拿 Style 当任务名 | `_task_label` 不再读 style，无名字回退任务 ID；新增回归 | `35bc330` |
| B4 可用功能清单生成项无条件 true | 检查引擎二进制 + server.json | `35bc330` |
| B6 RVC 成品/检查点同秒落盘 | 2 秒 mtime 容差；测试连续 5 次 + 全量通过 | `0895d41` |
| B8/T4 菜单无自动化回归 | 浏览器手工验证（双开→1、点空白→0），自动化列入 P2 | — |

## 已知限制（不阻塞发布）

- **B5/T1 预检启发式**：固定 7.0GB + 12 字/帧，只在 CPU 回退关闭时硬拦，可用 `YUE2_VULKAN_BUDGET_GB` 覆盖；大显存 Vulkan 可能误杀。已写入 README/CHANGELOG。
- **B7/T2 NAR 步数估算**：不是引擎真实回调，UI 标「约」。
- **B9/T5 LRC/转谱能力**：只检查模块/单权重，缺一半依赖时会显示可用；P2 改进。
- **T3 settings 损坏**：UI 有失败 toast，缺少可读的服务端提示；P2。
- 未在真实长歌上复验 warning→CPU 全链路；未在独显 Vulkan 上验证阈值。

## 验证

- `py312\python.exe -m unittest discover -s tests -p "test_*.py"` → **Ran 671 tests ... OK**
- 蓝军报告机检：`check-report: 9 条缺陷 ... 逐条取证通过`
- 第三方报告机检：`check-report: 5 条缺陷 ... 逐条取证通过`
- 长歌预检实测：4 秒失败、`:8080` 无监听，不启动引擎。
