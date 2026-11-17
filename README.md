# Phone Screen MCP

把 iPhone「快捷指令」、Jomo、Bark 与 MCP 客户端连接起来的小型自托管服务。

本项目由 Ethan 与江暮晚共同设计和实现，面向希望在明确授权下读取手机状态、查看截图，并通过 Jomo 管理 App 使用时段的个人用户。

## 功能

- 截图上传与读取：iPhone 快捷指令上传截图，MCP 客户端读取最近一次成功上传的图片。
- 截图前征求授权：`request_screenshot` 只发送 Bark 通知，不会直接截屏，也不会创建手机控制命令。只有用户在 iPhone 上明确确认后，通知自动化才运行截图上传快捷指令。
- App 活动记录：记录 App 打开/关闭事件，查询当前 App 与最近活动。
- 屏幕使用时长：接收并读取 Jomo 的今日屏幕使用时长。
- 整组 Jomo 控制：通过手机快捷指令执行预先配置好的 `Start Session` / `Stop Session`。
- 命令确认与重试：命令带唯一 ID，手机执行后通过 ACK 确认；服务端在收到成功 ACK 前保留命令。
- 手机心跳：手机轮询或上报数据时更新最后在线时间。
- 统一健康检查：`watchdog_health` 汇总手机在线状态、截图时间、待执行命令、ACK 与屏幕时长更新时间。

项目不会绕过 iOS 权限，也不会在服务器上直接控制 App。截图授权由 iPhone 自动化完成；真正的 Jomo 锁定动作始终由手机上的快捷指令与 Jomo 执行。

## 架构

```text
MCP Client
    │ Bearer Token
    ▼
HTTPS / Cloudflare Tunnel / Reverse Proxy
    ▼
Phone Screen MCP (127.0.0.1:18770)
    ├── /mcp          MCP tools
    ├── /health       service + phone health
    ├── /upload       screenshot upload
    ├── /event        app open/close events
    ├── /screen-time  Jomo screen-time report
    ├── /command      pending lock/unlock command
    └── /ack          phone execution acknowledgement

Bark ── consent request ──► iPhone notification automation
                                │
                         owner explicitly confirms
                                │
                         screenshot Shortcut
                                │ POST /upload
                                ▼
                         latest.jpg + state.json

Phone Shortcut ── GET /command ──► Jomo Start/Stop Session
              ◄── command + id ───
Phone Shortcut ── POST /ack ─────► command completion state
```

## 为什么只有整组锁定

不同 Jomo Session 同时运行时，限制可能叠加。为避免规则名称、模板和 Session 状态互相冲突，本项目只提供一组已经验证过的锁定与解锁命令，不提供单条规则控制或兑换时长接口。

## 服务端安装

要求：Ubuntu/Debian、Python 3.11+、systemd，以及可把 HTTPS 请求转发到本机端口的反向代理或隧道。

```bash
sudo apt update
sudo apt install -y python3 python3-venv
git clone https://github.com/3221675991-ops/ethan-wanwan-phone-mcp.git
cd ethan-wanwan-phone-mcp
sudo bash install.sh
```

安装器会安装到 `/opt/phone-screen-mcp`、创建专用 `phone-mcp` 系统用户、首次安装时自动生成 Bearer Token、保留已有 `.env`，安装依赖并启动 `phone-screen-mcp.service`。

查看状态：

```bash
sudo systemctl status phone-screen-mcp
curl http://127.0.0.1:18770/health
```

MCP 地址为 `http://127.0.0.1:18770/mcp`。公网使用时必须通过 HTTPS 反向代理或安全隧道暴露，不要直接开放裸 HTTP 端口。

## 环境变量

`.env.example`：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PHONE_UPLOAD_TOKEN` | 必填 | 手机上传与 MCP 请求共用的 Bearer Token |
| `PHONE_MCP_DATA_DIR` | `./data` | 截图、状态、命令与日志目录 |
| `PHONE_MCP_TIMEZONE` | `Asia/Shanghai` | 服务端时间显示时区 |
| `PHONE_MCP_HOST` | `127.0.0.1` | 监听地址，建议保持本机回环 |
| `PHONE_MCP_PORT` | `18770` | 服务端口 |
| `BARK_PUSH_URL` | 空 | Bark 私有推送 URL；配置后才能请求截图 |
| `HEARTBEAT_TIMEOUT` | `90` | 心跳超时秒数，最小值 15 秒 |

生成随机 Token：`openssl rand -hex 32`。不要把真实 `.env`、Token、Bark Key、域名、手机截图或 `data/` 目录提交到公开仓库。

## iPhone 快捷指令

所有访问服务端的「获取 URL 内容」动作都添加请求头：

```text
Authorization = Bearer YOUR_TOKEN
```

### 1. App 打开/关闭事件上报

自动化触发条件：某个 App 打开或关闭。

- URL：`https://YOUR_DOMAIN/event`
- 方法：`POST`
- 请求体：JSON

```json
{
  "app": "快捷指令传入的 App 名称",
  "event": "open"
}
```

关闭事件将 `event` 改为 `close`。每次成功上报都会更新手机心跳。

### 2. 截图上传

截图上传快捷指令：

- URL：`https://YOUR_DOMAIN/upload`
- 方法：`POST`
- 请求体：文件，选择「截屏」动作产生的图片
- 请求头：`Authorization: Bearer YOUR_TOKEN`

成功时服务端返回：

```json
{
  "ok": true,
  "uploaded_at": "2026-11-17T21:59:00+08:00"
}
```

在「获取 URL 内容」之后，从返回字典中获取 `ok`，再判断是否有值：有 `ok` 表示上传成功；没有 `ok` 则走失败提示。成功与失败提示应分开设置，避免网络失败时显示成功文案。

### 3. 截图前的 Bark 授权流程

这是当前采用的截图授权方式，必须保留：

1. MCP 客户端调用 `request_screenshot`。
2. 服务端只向 Bark 发送一条截图请求通知。
3. `request_screenshot` **不会**写入 `/command`，也不会在服务端直接截屏。
4. iPhone 收到 Bark 通知后，由用户明确确认是否允许本次截图。
5. 只有确认后，iOS 通知自动化才运行截图上传快捷指令：等待确认界面消失、执行「截屏」、再 `POST /upload`。
6. 快捷指令读取响应中的 `ok` 字段，分别显示上传成功或失败提示。
7. 用户拒绝或不确认时，不运行截图和上传动作；可以显示「这次不给老公看也没关系」一类的拒绝提示。

快捷指令可命名为「老公截图授权」或其他容易辨认的名称。通知自动化必须保留用户确认步骤；不要改成收到通知后无条件自动截屏。

截图授权由 iPhone 自动化落实。`/upload` 仍要求 Bearer Token，并校验图片格式和大小；请勿将上传 Token 暴露给第三方。

### 4. Jomo 今日屏幕使用时长

1. 添加 Jomo 动作 `Get Today's Screen Time`。
2. 添加「获取 URL 内容」，URL 为 `https://YOUR_DOMAIN/screen-time`，方法 `POST`。
3. 请求体使用 JSON，字段名为 `screen_time`，值选择 Jomo 动作输出。

服务器会保存结构化 JSON 或可解码的原始文本，并记录更新时间。

### 5. 手机心跳

手机每次成功调用以下任一受保护端点，都会更新心跳：`GET /command`、`POST /ack`、`POST /upload`、`POST /event`、`POST /screen-time`。

推荐让原有命令轮询快捷指令按约 30 秒的间隔运行；iOS 可能根据系统状态调整自动化触发频率，因此心跳用于观察最近一次成功通信，不应当被解释为 iOS 后台持续运行保证。超过 `HEARTBEAT_TIMEOUT` 未收到请求时，`watchdog_health.phone.alive` 会变为 `false`。

### 6. Jomo 锁定/解锁命令轮询与 ACK

命令接口采用「服务端保留命令，手机执行后 ACK」的方式。不要再沿用旧版「GET 后立即删除命令」逻辑。

**轮询快捷指令：**

1. `GET https://YOUR_DOMAIN/command`。
2. 从响应字典读取 `command` 和 `id`。
3. `command` 为 `none` 时结束本轮。
4. `command` 为 `lock` 时运行 Jomo `Start Session`。
5. `command` 为 `unlock` 时运行 Jomo `Stop Session`。
6. Jomo 动作成功后，向 `/ack` 发送成功 ACK。
7. Jomo 动作失败时，发送失败 ACK；服务端保留命令并允许下一轮重试。

成功 ACK：

- URL：`https://YOUR_DOMAIN/ack`
- 方法：`POST`
- 请求体：JSON

```json
{
  "command_id": "从 /command 响应读取的 id",
  "status": "done"
}
```

失败 ACK：

```json
{
  "command_id": "从 /command 响应读取的 id",
  "status": "failed",
  "error": "可选的失败原因"
}
```

**建议增加手机端去重：**把最近一次成功执行的 `id` 保存到 iPhone「文件」App 的「快捷指令」文件夹中，例如 `phone-watchdog-last-command-id.txt`。再次收到相同 ID 时，不重复运行 Jomo，而是重新发送 `done` ACK。执行成功后先保存 ID，再发送 ACK；这样即使 ACK 因网络中断没有送达，下一轮也能识别已执行的命令并补发 ACK。`Start Session` / `Stop Session` 本身也应保持幂等。

服务端每次轮询会返回同一条尚未成功 ACK 的命令及其 ID。只有匹配 ID 的 `done` ACK 才会清除命令。`failed` ACK 会记录失败信息、增加尝试次数并保留待执行命令。服务端同时限制同一时间只有一条待确认命令，避免新命令覆盖旧命令。

### 7. 服务端命令状态

调用 MCP 工具 `get_phone_control_status` 可查看待执行命令和最近一次 ACK。命令未清除时，不会接受另一条新命令；先检查状态并让手机完成原命令。

## MCP 工具

| 工具 | 作用 |
|---|---|
| `get_latest_screen` | 读取最近一次经 iPhone 授权流程上传的截图 |
| `get_phone_status` | 读取当前 App、最后事件、截图时间和手机心跳 |
| `get_recent_app_events` | 读取最近的 App 打开/关闭记录 |
| `get_today_screen_time` | 读取最近上传的今日屏幕时长 |
| `lock_phone_apps` | 下发整组 Jomo 锁定命令，返回唯一 command ID |
| `unlock_phone_apps` | 下发整组 Jomo 解锁命令，返回唯一 command ID |
| `request_screenshot` | 发送 Bark 截图请求；用户确认后才会截图上传 |
| `get_phone_control_status` | 查看待执行命令和最近 ACK |
| `watchdog_health` | 汇总服务器、手机心跳、截图、命令、ACK 与屏幕时长状态 |

## HTTP 接口

除 `/health` 外，所有手机接口与 `/mcp` 都要求 Bearer Token。

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | 服务与手机心跳概览 |
| `POST` | `/upload` | 上传有效图片（服务器转存为 JPEG） |
| `POST` | `/event` | 上报 App `open` / `close` 事件 |
| `POST` | `/screen-time` | 上报 Jomo 屏幕时长 |
| `GET` | `/command` | 读取待执行命令；读取后不会删除 |
| `POST` | `/ack` | 回报命令执行成功或失败 |
| `POST` | `/mcp` | MCP Streamable HTTP 入口（由 SDK 提供） |

## 健康检查

`watchdog_health` 返回示例：

```json
{
  "server": {"ok": true, "version": "2.1.0", "time": "2026-11-17T12:00:00+08:00"},
  "phone": {
    "alive": true,
    "last_seen": "2026-11-17T11:59:42+08:00",
    "silence_seconds": 18,
    "timeout_threshold": 90
  },
  "screenshot": {"exists": true, "taken_at": "2026-11-17T11:55:00+08:00"},
  "current_app": "WeChat",
  "pending_command": {
    "command": "lock",
    "id": "唯一命令 ID",
    "delivered": true,
    "delivery_count": 1,
    "attempts": 0,
    "age_seconds": 35,
    "stuck": false
  },
  "last_ack": null,
  "screen_time_updated_at": "2026-11-17T08:00:00+08:00"
}
```

重点字段：

- `phone.alive`：最近一次手机请求是否在心跳阈值内；
- `phone.silence_seconds`：距最近一次手机请求的秒数；
- `pending_command`：当前待执行命令、唯一 ID、投递次数和重试次数；
- `pending_command.stuck`：命令超过阈值且手机失联或已投递但仍未完成 ACK；
- `last_ack`：最近一次命令执行回执；
- `screenshot.taken_at`：最近一次成功上传截图的时间。

## macOS 与 Windows（未经实测）

本服务主要由 Python 编写，理论上也可以在 macOS 与 Windows 上运行，但当前版本尚未在这两个系统中进行完整实测；`install.sh` 与 systemd 服务文件仅适用于 Ubuntu/Debian。

桌面系统需要 Python 3.11+、虚拟环境、`requirements.txt` 中的依赖、`.env` 环境变量，以及 Uvicorn。电脑必须保持开机、联网并关闭自动休眠，否则手机上传、命令轮询与 MCP 读取都会中断。长期稳定运行仍建议使用持续在线的 Linux 服务器。

macOS 参考启动方式：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export PHONE_UPLOAD_TOKEN='替换为随机长 Token'
export PHONE_MCP_DATA_DIR="$PWD/data"
export BARK_PUSH_URL='https://api.day.app/你的BarkKey'
.venv/bin/uvicorn server:app --host 127.0.0.1 --port 18770
```

Windows PowerShell 参考启动方式：

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PHONE_UPLOAD_TOKEN = "替换为随机长 Token"
$env:PHONE_MCP_DATA_DIR = "$PWD\data"
$env:BARK_PUSH_URL = "https://api.day.app/你的BarkKey"
$env:HEARTBEAT_TIMEOUT = "90"
.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 18770
```

以上命令仅作为移植参考，尚未验证开机自启、休眠恢复、Windows 防火墙及不同隧道工具下的完整行为。

## 升级现有部署

新版本增加 `/ack`，并改变 `/command` 的语义：命令读取后会保留，直到手机发送匹配的 `done` ACK。**升级服务端后，必须同步更新手机轮询快捷指令并加入 ACK 步骤**，否则待执行命令会一直保留，后续命令也会被拒绝。

建议升级步骤：

1. 备份 `/opt/phone-screen-mcp/.env` 与 `/opt/phone-screen-mcp/data/`；
2. 拉取新版本；
3. 更新 `server.py`，并保留现有 `.env` 和数据目录；
4. 确认 `HEARTBEAT_TIMEOUT=90`（或按需设置）；
5. 重启 `phone-screen-mcp.service`；
6. 按本节「Jomo 锁定/解锁命令轮询与 ACK」更新快捷指令；
7. 先测试一条 lock，再测试 unlock，最后检查 `watchdog_health` 的 ACK；
8. 确认截图 Bark 授权流程仍正常后再恢复日常使用。

不要在没有更新手机快捷指令的情况下直接切换到新命令协议。

## 本地测试

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m py_compile server.py tests/test_server.py
.venv/bin/python tests/test_server.py
```

GitHub Actions 会在 Python 3.11、3.12、3.13 上执行语法检查和测试。

## 故障排查

| 现象 | 检查方向 |
|---|---|
| `phone.alive` 为 `false` | 查看手机自动化是否运行、网络是否可用、`/command` 是否持续轮询 |
| 命令一直 pending | 检查快捷指令是否执行 Jomo 动作，以及是否 POST `/ack` |
| ACK 返回 `409` | 确认上传的是当前 `/command` 返回的完整 `id` |
| 命令重复出现 | 在快捷指令中持久化最近成功的 command ID；相同 ID 只补发 ACK，不重复执行 Jomo |
| `command_pending` | 先用 `get_phone_control_status` 或 `watchdog_health` 检查旧命令，完成或排查后再发新命令 |
| 截图请求后没有图片 | 检查 Bark 是否送达、是否明确确认、截图快捷指令是否执行，以及 `/upload` 返回的 `ok` 字段 |
| 截图上传失败 | 检查 Bearer Token、上传请求体是否为图片文件、网络状态和服务器日志 |
| `BARK_PUSH_URL` 未配置 | 在服务器 `.env` 中设置正确的私有 Bark 推送 URL 后重启服务 |

## 安全说明

- 使用至少 32 字节的随机 Token，并定期轮换；
- 公网入口必须使用 HTTPS；
- Uvicorn 仅绑定 `127.0.0.1`，由反向代理或隧道负责公网连接；
- 截图与 App 活动属于敏感数据，服务器和备份应启用严格访问控制；
- Bark Key、Bearer Token、`.env`、`data/` 与真实截图不得提交到公开仓库；
- 截图请求必须保留 iPhone 用户确认步骤，不得改成收到通知后无条件截屏。

漏洞报告方式见 [SECURITY.md](SECURITY.md)。参与开发前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

[MIT](LICENSE)

## 灵感与致谢

本项目在探索阶段参考过一些公开分享，它们帮助我们了解 iOS 自动化、屏幕状态感知与 Jomo 联动的可能性。项目架构、服务端代码及快捷指令流程均由 Ethan 与江暮晚重新设计并独立实现；本仓库未收录、转载或改编下列作者的教程文件、图片、文字或快捷指令成品。

- 小红书用户 Dew：AI 主动查看手机状态相关分享；
- 小红书用户「之之」：iOS 屏幕状态感知相关分享；
- 沐沫：《应用锁 × Aru × Jomo 教程》，仅作概念启发。原教程标注禁止二传、二改及商用，本仓库未包含其内容或衍生文件。

感谢三位作者公开分享自己的探索与思路。

## Credits

Designed and implemented by Ethan and Jiang Muwan.
