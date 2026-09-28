# 音乐工作台 · 局域网开放 / 收回（配套：开放局域网.bat、关闭局域网.bat 双击调用）
#
# 干什么：把 dsh 工作台端口（默认 3081）从"只听本机回环"透到本机局域网地址，
#         只对该端口开一条入站防火墙规则，并把本机名单三行（local_env.bat）置为
#         开/关；最后直接打印"给其它电脑用的完整地址（含 token）"并复制到剪贴板。
#
# 为什么不直接让 dsh 监听 0.0.0.0：dsh 明确拒绝（--host 0.0.0.0 是 usage error，
# 理由是它带能读写本机文件的 agent 工具）。而把 dsh 绑到局域网 IP 又会连带弄坏
# 两处依赖回环的东西：看门狗探的是 http://127.0.0.1:3081/，工作台内部代理连的是
# 127.0.0.1:7863。所以保持 dsh 原样绑回环，只在这一层做 TCP 转发。
# 实测：绑 192.168.1.6:3081 与 dsh 的 127.0.0.1:3081 可共存，不冲突。
#
# 转发是纯 TCP 层，不改 Host/Origin 头。因此还要两个"名单"配套，缺一即
# "页面能打开、按钮全 403"：
#   - dsh 侧：--trusted-host（scripts\启动dsh工作台.bat 读 YUE2_LAN_HOSTS 自动加）
#   - 网关侧：YUE2_ALLOW_LAN=1 + YUE2_LAN_HOSTS（secrets\local_env.bat，本脚本会翻）
# 名单改了要重启工作台才生效（dsh 与网关都在启动时读环境变量），所以默认不擅自重启——
# 正在跑生成时重启会把任务打断；要立刻生效就加 -Restart。
#
# 故意**不**给网关端口（7863）开防火墙规则：局域网设备只经 3081 的内部代理访问网关，
# 网关虽因 LAN 模式绑了 0.0.0.0，但入站被防火墙挡在外，等于少一个无鉴权入口。
# （这条设计说明只写进日志，不再占屏幕行。）
#
# 屏幕与日志的分工（2026-09-29 改版）：屏幕上只有"标题 + 每步一行对勾 + 分享地址 +
# 三条提示"，一眼看完；所有带时间戳的流水（netsh 原文、改了哪几行、读自哪个文件、
# 复核结果）统统进 runtime\data\logs\lan-open.log。排障看日志，看结果看屏幕。
#
# 用法：
#   powershell -File 开放局域网.ps1                  # 开放（非管理员时自动提权）
#   powershell -File 开放局域网.ps1 -Preview         # 只打印地址，什么都不改，不用管理员
#   powershell -File 开放局域网.ps1 -Preview -Revoke # 只读预演"收回会动哪些东西"
#   powershell -File 开放局域网.ps1 -Restart         # 开放并重启工作台让名单生效
#   powershell -File 开放局域网.ps1 -Revoke          # 收回（同时把名单三行注释掉）
#   powershell -File 开放局域网.ps1 -Revoke -KeepEnv # 只拆转发和防火墙，不动名单
param(
    [switch]$Revoke,
    [switch]$Preview,      # 只算并打印分享地址，不改系统
    [switch]$KeepEnv,      # 收回时不碰 local_env.bat
    [switch]$Restart,      # 让名单/参数立刻生效（会中断正在跑的任务）
    [switch]$Elevated,     # 内部用：提权重跑时置位，避免无限弹 UAC
    [switch]$Hold          # 内部用：提权出来的新窗口结束前等一下，别把地址闪没了
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot          # scripts\.. = 仓库根
$LogDir = Join-Path $Root 'runtime\data\logs'
$LogFile = Join-Path $LogDir 'lan-open.log'

function Log([string]$msg) {
    # 明细只进文件，不上屏（屏幕那套见下面的呈现层）
    $line = "[{0}] {1}" -f (Get-Date -Format 'MM-dd HH:mm:ss'), $msg
    try {
        if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
        Add-Content -Path $LogFile -Value $line -Encoding UTF8
    } catch { }
}

# --------------------------------------------------------------------------- //
# 屏幕呈现层：标题一行、每步一行 √/×、地址一块、提示三行。
#
# 对齐用"视觉宽度"而不是字符数：中文在控制台里占两格，'防火墙' 与 '名单' 差 2 个
# 字但差 4 格，按 .Length 补齐会歪（实测歪过一次）。0x2E80 起按全角算。
# √ × 都收在 GBK 里，另外还有个前提：配套 .bat 进门就 chcp 65001。
# --------------------------------------------------------------------------- //
$DASH = [string][char]0x2500
function Vis([string]$s) {
    $w = 0
    foreach ($ch in $s.ToCharArray()) { if ([int]$ch -ge 0x2E80) { $w += 2 } else { $w += 1 } }
    return $w
}
function Rule { Write-Host ('  ' + ($DASH * 58)) -ForegroundColor DarkGray }
function Head([string]$title) {
    Write-Host ''
    Write-Host ('  ' + $title) -ForegroundColor White
    Rule
}
function Step([bool]$ok, [string]$label, [string]$detail) {
    $mark = if ($ok) { '√' } else { '×' }
    Write-Host ('  ' + $mark + ' ') -ForegroundColor $(if ($ok) { 'DarkGreen' } else { 'DarkRed' }) -NoNewline
    Write-Host ($label + (' ' * [Math]::Max(2, 8 - (Vis $label))) + $detail)
}
function Memo([string]$label, [string]$detail) {
    Write-Host ('    ' + $label + (' ' * [Math]::Max(2, 8 - (Vis $label))) + $detail) -ForegroundColor DarkGray
}
function Tail { Memo '明细' 'runtime\data\logs\lan-open.log' }

# ---- 端口唯一真源：ports.json ----
$dshPort = 3081
$gwPort = 7863
try {
    $j = Get-Content (Join-Path $Root 'ports.json') -Raw | ConvertFrom-Json
    if ($j.dsh) { $dshPort = [int]$j.dsh }
    if ($j.gateway) { $gwPort = [int]$j.gateway }
} catch { Log "ports.json 读不到，回退 7863/3081：$($_.Exception.Message)" }

$RuleName = "YuE2 工作台 · 局域网 $dshPort"
$EnvKeys = @('YUE2_HOST', 'YUE2_ALLOW_LAN', 'YUE2_LAN_HOSTS')

# 直接跑本 ps1（不经 .bat）且提权到新窗口时，结束前等一下，否则地址刚打印完窗口就闪没。
# 经 .bat 调用时由 bat 自己的收尾留窗，不需要 Hold。
function Wait-Hold {
    if ($Hold) {
        Write-Host ''
        try { Read-Host '按回车关闭本窗口' } catch { Start-Sleep -Seconds 60 }
    }
}

# ---- 本机局域网 IPv4：取默认路由所在网卡，绕开 WSL/Hyper-V 虚拟网卡 ----
function Get-LanIp {
    try {
        $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop |
                 Sort-Object RouteMetric, ifMetric | Select-Object -First 1
        if (-not $route) { return $null }
        return (Get-NetIPAddress -InterfaceIndex $route.ifIndex -AddressFamily IPv4 -ErrorAction Stop |
                Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
                Select-Object -First 1).IPAddress
    } catch { return $null }
}

# ---- 名单开关：secrets\local_env.bat 里那三行，开放=去掉 rem，收回=加上 rem ----
# 只动这三个已知变量行，别的一概不碰（那文件里还有密钥）；首次改动前留 .bak。
# 写盘按"无 BOM 的 UTF-8 + CRLF"整体回写：ASCII 会把文件里的中文注释变成问号，
# 而带 BOM 的 UTF8 又会让 cmd 把第一行 @echo off 认不出来。
# 返回 @{ok; text; changed}：text 给屏幕那一行用，明细（改了哪几行、备份在哪）进日志。
function Get-LanEnvToggle([string[]]$lines, [bool]$Enable) {
    # 纯函数：返回 [需改动的行号数组]，Preview 用它只读地报状态，Set-LanEnv 用它落盘
    $todo = @()
    for ($i = 0; $i -lt $lines.Count; $i++) {
        foreach ($k in $EnvKeys) {
            if ($Enable -and $lines[$i] -match "^\s*rem\s+set\s+$k=") { $todo += $i }
            elseif ((-not $Enable) -and $lines[$i] -match "^\s*set\s+$k=") { $todo += $i }
        }
    }
    return ,$todo
}
function Get-LanEnvState {
    # 只读：那三行现在是开着还是关着（只报状态，不掺"会怎么改"，措辞由调用方拼）
    $f = Join-Path $Root 'secrets\local_env.bat'
    if (-not (Test-Path $f)) { return '名单文件不存在' }
    $lines = @(Get-Content $f)
    $on = 0; $have = 0
    foreach ($k in $EnvKeys) {
        $hit = @($lines | Where-Object { $_ -match "^\s*(rem\s+)?set\s+$k=" })
        if ($hit.Count -eq 0) { continue }
        $have++
        if ($hit[0] -match '^\s*set\s') { $on++ }
    }
    if ($have -lt 3) { return "只找到 $have/3 行" }
    if ($on -eq 3) { return '开启' }
    if ($on -eq 0) { return '关闭' }
    return "部分开启（$on/3）"
}
function Set-LanEnv([bool]$Enable) {
    $f = Join-Path $Root 'secrets\local_env.bat'
    if (-not (Test-Path $f)) {
        Log "本地名单文件不存在：$f（跳过，请自行配置）"
        return @{ ok = $false; changed = $false; text = '名单文件不存在，请配 secrets\local_env.bat' }
    }
    $lines = @(Get-Content $f)
    $todo = Get-LanEnvToggle $lines $Enable
    if ($todo.Count -eq 0) {
        Log ("名单三行本来就是{0}状态，未改动" -f $(if ($Enable) { '开启' } else { '关闭' }))
        return @{ ok = $true; changed = $false; text = $(if ($Enable) { '三行已是开启状态' } else { '三行已是注释状态' }) }
    }
    if (-not (Test-Path "$f.bak")) { Copy-Item $f "$f.bak" }
    foreach ($i in $todo) {
        if ($Enable) { $lines[$i] = ($lines[$i] -replace '^\s*rem\s+', '') }
        else { $lines[$i] = 'rem ' + $lines[$i].TrimStart() }
    }
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($f, (($lines -join "`r`n") + "`r`n"), $enc)
    Log ("名单三行已{0}（改动 {1} 行，备份在 secrets\local_env.bat.bak）" -f $(if ($Enable) { '放开' } else { '注释掉' }), $todo.Count)
    Log '名单是启动时读的，要重启工作台才生效（本脚本加 -Restart，或双击 scripts\启动音乐工作台.bat）'
    return @{
        ok = $true; changed = $true
        text = ("三行已{0}（备份 local_env.bat.bak）" -f $(if ($Enable) { '放开' } else { '注释掉' }))
    }
}

# ---- HTTP 探一下，只拿状态码 ----
# 为什么不用 Invoke-WebRequest：PowerShell 5.1 的 Invoke-WebRequest 处理 dsh 那条
# "带有效 token → 303 带 Set-Cookie"的响应时会抛 NullReferenceException（不管把
# -MaximumRedirection 设成 0 还是让它跟随重定向都一样），照原样判会把**有效 token**
# 读成"连不上"。这里直接用 .NET HttpWebRequest 关掉自动重定向，拿到原始状态码。
function Probe-Http([string]$url, [int]$timeoutSec = 8) {
    try {
        $req = [System.Net.HttpWebRequest]::Create($url)
        $req.Method = 'GET'
        $req.AllowAutoRedirect = $false
        $req.Timeout = $timeoutSec * 1000
        $resp = $req.GetResponse()
        $code = [int]$resp.StatusCode
        $resp.Close()
        return $code
    } catch [System.Net.WebException] {
        $r = $_.Exception.Response
        if ($r) { $code = [int]$r.StatusCode; $r.Close(); return $code }
        return 0
    } catch { return 0 }
}

# ---- 分享地址：取日志里最后一条 token，先向本机回环实测它还有没有效 ----
# dsh 的 token 每次启动都换，让人手抄日志里 43 位字符串既容易错又没法验证。
# 注意：必须由用户自己把地址粘进浏览器地址栏（浏览器发起的顶层导航）。不要用 file://
# 书签或从别的网页跳转打开——鉴权 cookie 是 SameSite=Strict，跨站发起的导航里浏览器
# 不保存它，表现就是"明明带了 token 却仍提示 authentication required"。
function Show-Share([string]$lanIp, [switch]$NoRule) {
    $share = $null
    $note = ''
    $ok = $false
    $logF = Join-Path $Root 'dsh-plugin\_dsh_web.log'
    if (Test-Path $logF) {
        $m = Select-String -Path $logF -Pattern 'http://127\.0\.0\.1:\d+/\?token=([A-Za-z0-9_.\-]+)' |
            Select-Object -Last 1
        if ($m) {
            $token = $m.Matches[0].Groups[1].Value
            $share = "http://${lanIp}:${dshPort}/?token=$token"
            $code = Probe-Http "http://127.0.0.1:${dshPort}/?token=$token"
            if ($code -in @(200, 302, 303)) { $ok = $true; $note = '实测有效'; Log "token 实测状态码 $code（有效）" }
            elseif ($code -eq 0) { $note = "本机工作台 :$dshPort 没在跑——先启动工作台，再双击 开放局域网.bat 取地址" }
            else { $note = "已被拒（HTTP $code），多半是 dsh 之后重启过；启动工作台后重跑本脚本可取新的" }
        }
    }
    if (-not $share) {
        $share = "http://${lanIp}:${dshPort}/"
        $note = "日志里还没有（工作台未启动）；启动后重跑可自动带上"
    }
    # 预览模式上面没有步骤流水，不必再画一条分隔线
    if (-not $NoRule) { Rule }
    Write-Host ''
    Write-Host ('    ' + $share) -ForegroundColor Cyan
    Write-Host ''
    $copied = $false
    try { Set-Clipboard -Value $share; $copied = $true } catch { Log '剪贴板不可用，请手工复制上面的地址' }
    Step $ok 'token' $(if ($copied) { $note + ' · 已复制到剪贴板' } else { $note })
    Memo '用法' '粘到对方浏览器地址栏后回车（别从别的页面点跳转）'
    Memo '收回' '双击 scripts\关闭局域网.bat'
    Log "分享地址：$share（$note）"
}

# ---- 读转发表：netsh 的输出是"地址 端口 地址 端口"四列，**中间没有冒号** ----
# 原来按 ":3081" 去匹配整行，结果一条也匹配不上——收回时看着"没有转发"报了成功，
# 实际规则还原封不动挂在 192.168.1.6:3081 上（实测抓到）。所以按列解析。
function Get-PortProxyRules {
    $out = @()
    foreach ($line in @(netsh interface portproxy show all)) {
        $c = @($line -split '[\s::]+' | Where-Object { $_ -ne '' })
        if ($c.Count -lt 4) { continue }
        if ($c[0] -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { continue }
        if ($c[2] -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { continue }
        $out += [pscustomobject]@{
            Listen = $c[0]; ListenPort = [int]$c[1]
            Connect = $c[2]; ConnectPort = [int]$c[3]
        }
    }
    return $out
}

# ---- 重启工作台：只杀"本机自己绑的"监听进程，看门狗会在 ~35s 内拉起 ----
# 坑：:$dshPort 上还有 portproxy 的监听者，宿主是 IP Helper 的 svchost。按端口乱杀会把
# svchost 干掉，iphlpsvc 一倒所有 portproxy 规则一起丢，所以必须按 LocalAddress 过滤。
function Restart-Workbench {
    Log '正在重启工作台（名单/参数要重启才生效；正在跑的任务会被打断）'
    $killed = 0
    foreach ($pt in @($dshPort, $gwPort)) {
        $own = @(Get-NetTCPConnection -State Listen -LocalPort $pt -ErrorAction SilentlyContinue |
                 Where-Object { $_.LocalAddress -in '127.0.0.1', '0.0.0.0', '::', '::0' })
        foreach ($c in $own) {
            try {
                Stop-Process -Id $c.OwningProcess -Force -ErrorAction Stop
                Log ("已停止 :{0} 上的本机进程 PID {1}（绑 {2}），看门狗会自动拉起" -f $pt, $c.OwningProcess, $c.LocalAddress)
                $killed++
            } catch { Log "停止 PID $($c.OwningProcess) 失败：$($_.Exception.Message)" }
        }
    }
    if ($killed -eq 0) {
        Memo '重启' '工作台本来就没跑，无需重启'
        return
    }
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Seconds 3
        $up = $true
        foreach ($pt in @($dshPort, $gwPort)) {
            $l = @(Get-NetTCPConnection -State Listen -LocalPort $pt -ErrorAction SilentlyContinue |
                   Where-Object { $_.LocalAddress -in '127.0.0.1', '0.0.0.0', '::', '::0' })
            if (-not $l) { $up = $false }
        }
        if ($up) {
            Step $true '重启' '工作台已重新监听，上面的地址立即可用'
            Log "工作台已重新监听 $dshPort/$gwPort"
            return
        }
    }
    Step $false '重启' "120s 没等齐两个端口——双击 scripts\启动音乐工作台.bat 手工启动"
}

# ---- 预览：不碰系统、不需要管理员，只把地址算出来打印 ----
# -Preview -Revoke 是"收回预演"：只报现在挂着什么、真收回会动哪些东西。
if ($Preview) {
    $lanIpP = Get-LanIp
    if (-not $lanIpP) {
        Head '音乐工作台 · 预览'
        Step $false '地址' '找不到默认路由上的局域网 IPv4'
        Tail
        Wait-Hold
        exit 1
    }
    $mode = if ($Revoke) { '收回预演（不动任何东西）' } else { '预览（不动任何东西）' }
    Head ('音乐工作台 · ' + $mode + '    :' + $dshPort + ' @ ' + $lanIpP)
    Log "===== 预览（未改动端口转发/防火墙/名单）：工作台 :$dshPort @ $lanIpP ====="
    $envSt = Get-LanEnvState
    if ($Revoke) {
        $pOut = @(Get-PortProxyRules | Where-Object { $_.ListenPort -eq $dshPort })
        # 注意 -join 的优先级低于 +：必须先括号把 -join 的结果括起来，再接后缀文案
        $pTxt = if ($pOut.Count) { (($pOut | ForEach-Object { "$($_.Listen):$dshPort" }) -join '、') + ' 在挂着，真收回会删' } else { ":$dshPort 上没有转发" }
        Memo '转发' $pTxt
        Memo '防火墙' $(if (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) { '规则还在，真收回会删' } else { '无本脚本建的规则' })
        Memo '名单' $(if ($envSt -eq '开启') { '开启，真收回会注释这三行' } else { "$envSt，无需改动" })
        Tail
    } else {
        Memo '名单' $(if ($envSt -eq '开启') { '开启，无需改动' } else { "$envSt，开放时会放开这三行" })
        Show-Share $lanIpP -NoRule
        Tail
    }
    Wait-Hold
    exit 0
}

# ---- 需要管理员：没有就自己提权重跑一次（新窗口保持可见，结果别只留在日志里）----
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $Elevated) {
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-Elevated', '-Hold')
    if ($Revoke) { $argList += '-Revoke' }
    if ($KeepEnv) { $argList += '-KeepEnv' }
    if ($Restart) { $argList += '-Restart' }
    try {
        Start-Process powershell -Verb RunAs -ArgumentList $argList -Wait
        Memo '提权' '已在管理员窗口执行完（那份窗口里有地址）'
        Log '已以管理员身份执行完毕（明细见本日志）'
    } catch {
        Head '音乐工作台 · 未执行'
        Step $false '提权' '授权被取消，系统没有任何改动'
        Tail
    }
    Wait-Hold
    exit 0
}

# ---- 前置条件：netsh portproxy 由 IP Helper（iphlpsvc）承载 ----
# 实测踩过的坑：本机 iphlpsvc 处于 Stopped（但 StartMode 仍是 Auto），规则加得成功、
# netstat 上却没有任何监听，转发口连不上，症状和"防火墙没开"一模一样。所以在这里
# 把它当依赖管起来，而不是等人来查。启动类型被设为 Disabled 时不擅自改系统配置，
# 只把要执行的命令原样说破。
$svc = Get-Service -Name 'iphlpsvc' -ErrorAction SilentlyContinue
if (-not $svc) {
    Head '音乐工作台 · 无法开放'
    Step $false '服务' '本机没有 IP Helper（iphlpsvc），portproxy 不可用——需改用反向代理（nginx/caddy）'
    Tail
    Log '本机没有 IP Helper（iphlpsvc）服务，portproxy 不可用'
    exit 1
}
if ($svc.StartType -eq 'Disabled') {
    Head '音乐工作台 · 无法开放'
    Step $false '服务' 'IP Helper 启动类型被设为 Disabled，portproxy 不会监听'
    Memo '修复' 'Set-Service -Name iphlpsvc -StartupType Automatic; Start-Service iphlpsvc'
    Tail
    Log 'IP Helper 启动类型为 Disabled，已放弃开放'
    exit 1
}
if ($svc.Status -ne 'Running') {
    try {
        Start-Service -Name 'iphlpsvc' -ErrorAction Stop
        Log 'IP Helper（iphlpsvc）原为停止状态，已启动（portproxy 的承载服务）'
    } catch {
        Head '音乐工作台 · 无法开放'
        Step $false '服务' ("IP Helper 启动失败：" + $_.Exception.Message)
        Tail
        Log "IP Helper 启动失败：$($_.Exception.Message)"
        exit 1
    }
}

if ($Revoke) {
    Head '音乐工作台 · 收回局域网'
    Log '===== 收回局域网 ====='
    $ours = @(Get-PortProxyRules | Where-Object { $_.ListenPort -eq $dshPort })
    $deleted = @()
    $foreign = @()
    foreach ($p in $ours) {
        if ($p.Connect -eq '127.0.0.1' -and $p.ConnectPort -eq $dshPort) {
            $r = netsh interface portproxy delete v4tov4 listenaddress=$p.Listen listenport=$dshPort 2>&1
            $say = (($r | Out-String).Trim())
            # netsh 偶发回一句"系统找不到指定的文件"（实测抓到一次），但规则还在：等一秒重试
            if ($say -match 'cannot find the file|找不到') {
                Start-Sleep -Seconds 1
                $r = netsh interface portproxy delete v4tov4 listenaddress=$p.Listen listenport=$dshPort 2>&1
                $say = (($r | Out-String).Trim()) + '（已重试一次）'
            }
            Log ("删除转发 {0}:{1} -> {2}:{3} —— {4}" -f $p.Listen, $dshPort, $p.Connect, $p.ConnectPort, $say)
            $deleted += $p.Listen
        } else {
            $foreign += $p
            Log ("⚠ :$dshPort 上还有一条不是本脚本建的转发（目标 $($p.Connect):$($p.ConnectPort)），未动它——请自行确认")
        }
    }
    if (-not $ours) {
        Memo '转发' ":$dshPort 上本来就没有转发"
        Log "本机没有监听 :$dshPort 的端口转发（未动其它端口的规则）"
    } else {
        $still = @(Get-PortProxyRules | Where-Object { $_.ListenPort -eq $dshPort -and $_.Connect -eq '127.0.0.1' })
        if ($still.Count -eq 0) {
            if ($deleted.Count) {
                Step $true '转发' ("已删除 {0} 条（{1}）" -f $deleted.Count, ($deleted -join '、'))
            } else {
                Memo '转发' '本脚本格式的转发本来就没有'
            }
            Log '复核：本脚本格式的转发已全部清除'
        } else {
            Step $false '转发' ("还剩 {0} 条没删掉（{1}）——手工执行 netsh interface portproxy delete" -f `
                  $still.Count, (($still | ForEach-Object { $_.Listen }) -join ', '))
            Log ("⚠ 复核：:{0} 上还剩 {1} 条转发" -f $dshPort, $still.Count)
        }
        if ($foreign.Count) { Memo '转发' ("另有 {0} 条不是本脚本建的，未动（目标 {1}）" -f $foreign.Count, (($foreign | ForEach-Object { "$($_.Connect):$($_.ConnectPort)" }) -join ', ')) }
    }
    if (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
        Remove-NetFirewallRule -DisplayName $RuleName
        Step $true '防火墙' '规则已删除'
        Log "删除防火墙规则：$RuleName"
    } else {
        Memo '防火墙' '无本脚本建的规则'
        Log "无本脚本建的防火墙规则：$RuleName"
    }
    if ($KeepEnv) {
        Memo '名单' '-KeepEnv：未改动（网关仍绑 0.0.0.0 并认 LAN 名单）'
        Log '-KeepEnv：未改动 secrets\local_env.bat'
    } else {
        $ev = Set-LanEnv $false
        Step $ev.ok '名单' $ev.text
        if ($ev.changed -and -not $Restart) { Memo '提醒' '名单要重启工作台才生效（本脚本加 -Restart）' }
    }
    if ($Restart) { Restart-Workbench }
    Write-Host ''
    Write-Host ('  同网段设备已经连不上 :' + $dshPort + '；本机 127.0.0.1 上的工作台照常用')
    Tail
    Wait-Hold
    exit 0
}

$lanIp = Get-LanIp
if (-not $lanIp) {
    Head '音乐工作台 · 无法开放'
    Step $false '地址' '找不到默认路由上的局域网 IPv4'
    Tail
    Log '找不到默认路由上的局域网 IPv4，放弃'
    Wait-Hold
    exit 1
}
Head ('音乐工作台 · 开放局域网    ' + $lanIp + ':' + $dshPort)
Log "===== 开放局域网（工作台 :$dshPort @ $lanIp）====="

$ev = Set-LanEnv $true
Step $ev.ok '名单' $ev.text
if ($ev.changed -and -not $Restart) { Memo '提醒' '名单要重启工作台才生效（本脚本加 -Restart）' }

# ---- 与启动名单对一下口径，不一致直接说破，不留"静默 403" ----
# 先认进程环境变量；为空时再回读 local_env.bat（.bat 是先 call 名单再跑本脚本的，
# 若这三行刚被本脚本放开，环境变量里还是没有，直接报"为空"会让人白跑一趟重启）。
$allowList = @($env:YUE2_LAN_HOSTS -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$from = '环境变量'
if ($allowList.Count -eq 0) {
    $ef = Join-Path $Root 'secrets\local_env.bat'
    if (Test-Path $ef) {
        $mHosts = Select-String -Path $ef -Pattern '^\s*set\s+YUE2_LAN_HOSTS=(.+)$' | Select-Object -Last 1
        if ($mHosts) {
            $allowList = @($mHosts.Matches[0].Groups[1].Value -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
            $from = 'local_env.bat（刚放开，还没进环境变量）'
        }
    }
}
if ($allowList.Count -eq 0) {
    Step $false '白名单' "名单还没配：TCP 通了但 dsh/网关会 403，请填 secrets\local_env.bat 三行"
    Log '⚠ YUE2_LAN_HOSTS 为空：TCP 通了但 dsh/网关的同源名单还没开'
} elseif ($allowList -notcontains $lanIp) {
    Step $false '白名单' ("本机 $lanIp 不在名单（{0}）里，请求会被 403" -f ($allowList -join ', '))
    Log "⚠ 本机地址 $lanIp 不在 YUE2_LAN_HOSTS（$(($allowList) -join ', ')，读自$from）里：白名单对不上，请求会被 403"
} else {
    Step $true '白名单' ("本机 {0} 已在名单内" -f $lanIp)
    Log "白名单核对通过：$lanIp 在 YUE2_LAN_HOSTS 内（读自$from）"
}

# ---- 端口转发（幂等：先删同监听口再建）----
$same = @(Get-PortProxyRules | Where-Object { $_.Listen -eq $lanIp -and $_.ListenPort -eq $dshPort })
if ($same.Count) {
    netsh interface portproxy delete v4tov4 listenaddress=$lanIp listenport=$dshPort | Out-Null
    Log '已清理同端口旧转发'
}
netsh interface portproxy add v4tov4 listenaddress=$lanIp listenport=$dshPort `
    connectaddress=127.0.0.1 connectport=$dshPort | Out-Null
Step $true '转发' ("{0}:{1} → 127.0.0.1:{1}" -f $lanIp, $dshPort)
Log "端口转发：${lanIp}:$dshPort -> 127.0.0.1:$dshPort"

# ---- 防火墙：只放行这一个端口，且只在 Private 配置文件下 ----
if (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule -DisplayName $RuleName -Enabled True -Action Allow -Profile Private | Out-Null
    Step $true '防火墙' ("只放 :{0}，仅 Private 网络（规则已更新）" -f $dshPort)
    Log "防火墙规则已更新：$RuleName（仅 Private）"
} else {
    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $dshPort -Profile Private | Out-Null
    Step $true '防火墙' ("只放 :{0}，仅 Private 网络（规则已新建）" -f $dshPort)
    Log "防火墙规则已新建：$RuleName（仅 Private）"
}
Log '注意：网关端口未放行，属有意为之（局域网只能经 3081 的内部代理到网关）'

# ---- 自检：转发口在听、且 HTTP 真能答话 ----
Start-Sleep -Seconds 1
$listen = @(netstat -ano -p tcp | Select-String 'LISTENING' | Select-String ":$dshPort ")
$reach = Probe-Http "http://${lanIp}:$dshPort/"
Log ('转发口监听：{0}' -f $(if ($listen.Count) { "在听（$($listen.Count) 行）" } else { '未监听——检查 iphlpsvc 服务是否运行' }))
Log "HTTP 实测 http://${lanIp}:$dshPort/ ：$(if ($reach -eq 0) { '不可达' } else { "HTTP $reach" })"
if (-not $listen.Count) {
    Step $false '自检' "转发口没在听——查 IP Helper（iphlpsvc）是否在跑"
} elseif ($reach -eq 0) {
    Step $false '自检' ("http://{0}:{1} 连不上" -f $lanIp, $dshPort)
} elseif ($reach -eq 401) {
    Step $true '自检' "转发口在听，未登录回 401（栅栏在挡人，正常）"
} else {
    Step $true '自检' ("转发口在听，HTTP {0}" -f $reach)
}

Show-Share $lanIp
if ($Restart) { Restart-Workbench }
Tail
Log '===== 完成 ====='
Wait-Hold
