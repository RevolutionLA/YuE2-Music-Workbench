# 蓝军审查报告 — YuE2 音乐工作台 @ 533431f (master)

> 三方对抗式评审 · 蓝军(敌意审查)。审查方式:静态代码审查,未启动服务。

## 一、安全性

先说公道话再开打:这个库明显被蓝军锤过多轮(代码里大量 "蓝军 S3"、"评审 P1-2" 注释),路径穿越普遍做了 `os.path.basename` + `resolve()` 双保险(app.py:1158-1160, 1649-1653, 5545-5547),subprocess 全部列表传参、无 `shell=True`、全部带 timeout。但仍找到真实暴露面:

- **S-1 [P2][安全性][app.py:22,200-204 + settings.py:22]** 绑定地址由 `YUE2_HOST` 环境变量控制,默认 127.0.0.1;但开启 LAN 后网关绑 `0.0.0.0`(`settings.py:22`、`app.py:200-204` 打印建议改 0.0.0.0)。虽然 `scripts/开放局域网.ps1:20-21` 故意不给 7863 开防火墙规则,但**只要用户手动开过 Windows 防火墙、或防火墙策略被安全软件接管,7863 就是一个零鉴权入口**——可删文件(`DELETE /rvc/models/{name}` app.py:4152、`DELETE /rvc/train/{rid}`:7872)、杀进程(`/generate/stop`:2156、`taskkill`:1923)、耗尽 GPU。网关 API 全程无 token(对比 dsh 3081 有 token)。
- **S-2 [P2][安全性][app.py:114-119]** 代码自己承认:无 body 的 POST/DELETE 是浏览器简单请求,不做预检,任意网页 JS 可向 `http://127.0.0.1:7863/api/generate/stop`、`/api/rvc/train/pause/{rid}`、`DELETE /api/history/{rid}` 发请求并**生效**(只读不到响应)。`_guard_local_only`(app.py:252) 只校验 Host 头,防 DNS 重绑定不防 CSRF。恶意网页可静默停掉用户任务、清空历史。仅本机浏览器可达 → 降为 P2。
- **S-3 [P3][安全性][app.py:1614-1617, 1596, 1631]** `history_clear`/`history_delete` 执行 `(HIST_DIR / i.get("file", "")).unlink(missing_ok=True)`,文件名直接取自 history.json 条目,没有 basename/containment 校验。正常流程文件名自产(`_new_id()`),但 history.json 若被篡改(或未来某 API 写入未消毒名字)可删任意文件。防御纵深缺失。
- **S-4 [P3][安全性][app.py:393, 2024, 2979 等][结论]** 命令注入:未发现。所有 subprocess 均列表参数 + 固定字符串,`RVC_PY -c <script>` 中 script 为内嵌常量,用户输入只作 argv 传入(`str(p)`, `str(alpha)`),Python `-c` 脚本内部用 `sys.argv` 当路径用,无 eval。RVC_PY/RVC_DIR 来自配置非请求。
- **S-5 [P3][安全性][app.py:696][结论]** secrets 泄漏:`/system/info` 注释声明"不回密钥";grep 未见 API key 回传路由。dsh token 由 scripts 管理,网关不持有。未发现问题。

结论:**S-1、S-2、S-3**(S-1 需 LAN 模式+防火墙失守;S-2 仅本地单机使用者被恶意网页打;S-3 同左)。

## 二、可靠性

- **R-1 [P2][可靠性][watchdog.py:44-65]** 看门狗 `LONG_JOB_WINDOW=360s`:只要 `job.json`/`train.log` 等 mtime 在 360 秒内动过就**永不击杀**网关——即使网关真死了。而训练日志注释明说"每 200 步追加"(可能远超 6 分钟一轮),构成永久免死金牌分支:真死机 + 长任务在跑 = 服务永不自动恢复,只能人肉重启。判活文件粒度太粗。
- **R-2 [P3][可靠性][app.py:6032-6036]** `_RVC_TRAIN_PROC`、`_RVC_TRAIN_PAUSE_REQ` 全局变量,写点在训练线程(`RVC_TRAIN_LOCK` 保护 :6562 一带),但 pause 端点 :7828 读 `_RVC_TRAIN_PROC` 是否同样持锁未在片段内证实——若 pause 端点裸读则有 None/竞态窗口(terminate 已退出进程会抛异常)。**未完全验证,需确认 :7828-7869 是否持 RVC_TRAIN_LOCK**。
- **R-3 [P3][可靠性][app.py:1083, 5276-5284][结论]** 磁盘满/路径不存在:上传走流式+413 限额(:1517-1555),清理均有 `missing_ok=True`/`ignore_errors`;`_rvc_train_write` 原子重写。异常吞掉点(如 :6365-6366 GPU 健康写失败 `pass`)均为可容忍遥测。总体处理到位,未发现高危。

结论:R-1、R-2(后者部分未验证)。

## 三、性能

- **P-1 [P2][性能][app.py:1611, 2330]** `history_audio`、`generate_audio` 用 `Response(content=fp.read_bytes(), ...)` **整文件读进内存**再返回。歌曲 wav 数十 MB,多个客户端并发拉取(LAN 场景多人听歌)时内存峰值线性叠加,且这是同步 def(线程池,不卡事件循环但占内存)。应改 `FileResponse`(库内其他地方如 :5967 已用 FileResponse,证明标准存在,属不一致偷懒)。
- **P-2 [P3][性能][app.py:7964-7976, 7997-8000][结论]** async 处理器重活已系统性地丢 `run_in_threadpool`(save_voice/transcribe/denoise 都有专门注释),同步 def 路由天然在线程池。`rvc_convert`(:5065) 等 async 上传后落盘走 `_stream_upload_to`(async 分块,不阻塞)。**未发现卡事件循环的重活**。
- **P-3 [P3][性能][app.py:2321]** `/generate/list` 上限 1000 条全量返回 JSON;前端一次性渲染大列表未实测(见未验证项)。

结论:P-1。

## 四、功能正确性

- **F-1 [P3][功能][tests/test_review_fixes.py:31-47]** 测试通过 stub `main` 模块 + monkeypatch 目录跑 TestClient,契约覆盖较实(断言具体 JSON/状态码,非永绿)。但**测试默认 stub 掉 ffmpeg/CLI 探测**(:79-88 注释自认"作者机假通过"问题已修),意味着 CI 永远测不到真实子进程链路,PyMSS/RVC 真实链路无回归网。
- **F-2 [未发现问题]** lrc/lrc_align/voices 状态机:`voices.py:85-107` save 有名字校验,delete :110 有 found 判断;lrc 构建/状态查询(app.py:2352-2370)POST 起 run、GET 查 done 的两段式契约与前端轮询一致(前端代码未逐行验证)。API 返回契约抽查了 scores/score/rvc 三组,字段与 `/tasks/unified` 聚合一致。

结论:F-1。

## 五、UI / 主题 / 布局

- **U-1 [P3][UI][static/index.html:162-166, 514, 645-646, 913, 1367]** 深色主题用大量 `html[data-theme="dark"]` 逐条覆盖硬编码色(#30363d、#1d1003 等)。机制上两套主题均有完整变量集(:14-75 浅色、:111-161 深色,含 --shell 指向主区令牌的收敛修复),且 `test_design_system.py` 声称对对比度做了断言——设计纪律好于平均。但散落的 20+ 处深色硬编码是回归温床:新增组件若漏写 dark 覆盖即出现"浅色可读、深色不可读"。属于维护性风险而非当前可见缺陷。
- **U-2 [未发现 P0/P1]** 布局有 `@media (max-width:860px)`(:1369)降级;focus 态使用 `:focus-visible` 与 --acc-hi 焦点环(:550, :910 注释)。toast/sticky-notify 提供用户可见错误通道。小窗口溢出未实测(未验证项)。

结论:U-1。

## 六、测试真实性

- **T-1 [P3][测试][tests/test_review_fixes.py:76-88]** 好的一面:测试断言真实行为(正则 ID、目录清理 glob 断言、abc 变换具体音名),有 tearDown 还原 monkeypatch,明显不是永绿测试。坏的一面:关键重路由(/generate/start、/rvc/convert、/rvc/train)被注释明确排除在测试外("绝不触发任何计算"),即**核心业务链路零测试覆盖**,只有周边纯函数有网。

结论:T-1。

## 未验证项声明
- 未实际启动服务、未发任何 HTTP 请求、未跑 pytest/unittest(只读环境)。
- static/index.html 9200 行仅抽查 ~40 行片段;小窗口实际渲染、前端大列表性能未实测。
- app.py:7828 pause 端点是否持锁读 `_RVC_TRAIN_PROC` 未读完整段,R-2 为存疑项。
- watchdog.py 仅读前 120 行,击杀主循环后半段未审。
- src/ 15 个模块仅抽查 voices.py 与测试;lrc_align/sheetsage 等未审。
- 实际防火墙状态、YUE2_HOST 运行时取值未探测,S-1 触发条件按最坏假设。

## 汇总
- P0:无
- P1:无
- P2:S-1(LAN 下 0.0.0.0 + 网关零鉴权)、S-2(简单请求 CSRF 可停任务/删数据)、R-1(看门狗长任务免死窗口可致真死不重启)、P-1(音频接口整读内存)
- P3:S-3、R-2、P-3、F-1、F-2、U-1、T-1
