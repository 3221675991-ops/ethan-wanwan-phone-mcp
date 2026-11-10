# Phone Screen MCP

把 iPhone「快捷指令」、Jomo 与任意 MCP 客户端连接起来的小型自托管服务。

它可以：

- 接收并读取手机截图；
- 记录 App 打开/关闭事件；
- 接收并读取 Jomo 的今日屏幕使用时长；
- 下发整组 `lock` / `unlock` 命令，由手机快捷指令启动或停止一个预先配置好的 Jomo Session。

项目不会直接绕过 iOS 权限，也不会在服务器上控制 App。真正的锁定动作始终由手机上的快捷指令与 Jomo 执行。

## 为什么只有整组锁定

不同 Jomo Session 同时运行时，限制可能叠加。为避免规则名称、模板和 Session 状态互相冲突，本项目只提供一组已经验证过的锁定与解锁命令，不提供单条规则控制或兑换时长接口。

## 架构

1. iPhone 快捷指令把截图、App 事件和屏幕时长上传到服务器。
2. MCP 客户端通过工具读取服务器保存的状态。
3. MCP 客户端调用 `lock_phone_apps` 或 `unlock_phone_apps`。
4. 手机上的轮询快捷指令读取 `/command`，再调用 Jomo 的 Start Session / Stop Session。

## 服务端安装

要求：Ubuntu/Debian、Python 3.11+、systemd，以及可把 HTTPS 请求转发到本机端口的反向代理或隧道。

```bash
sudo apt update
sudo apt install -y python3 python3-venv
sudo bash install.sh
```

安装器会：

- 安装到 `/opt/phone-screen-mcp`；
- 创建专用的 `phone-mcp` 系统用户；
- 自动生成 Bearer Token；
- 启动 `phone-screen-mcp.service`。

查看状态：

```bash
sudo systemctl status phone-screen-mcp
curl http://127.0.0.1:18770/health
```

服务端 MCP 地址为：

```text
http://127.0.0.1:18770/mcp
```

公网使用时，请通过 HTTPS 反向代理或安全隧道暴露服务，不要直接开放裸 HTTP 端口。

## macOS 与 Windows（未经实测）

本服务主要由 Python 编写，理论上也可以在 macOS 或 Windows 上运行，但当前版本尚未在这两个系统中进行完整实测，`install.sh` 与 systemd 服务文件仅适用于 Ubuntu/Debian。

桌面系统需要满足以下条件：

- 安装 Python 3.11 或更高版本；
- 创建虚拟环境并安装 `requirements.txt`；
- 设置 `.env.example` 中列出的环境变量；
- 使用 Uvicorn 启动 `server:app`；
- 配置能转发到本机 `18770` 端口的 HTTPS 反向代理或安全隧道；
- 电脑保持开机、联网，并关闭自动休眠，否则手机上传、命令轮询和 MCP 读取都会中断。

macOS 参考启动方式：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export PHONE_UPLOAD_TOKEN='替换为随机长 Token'
export PHONE_MCP_DATA_DIR="$PWD/data"
.venv/bin/uvicorn server:app --host 127.0.0.1 --port 18770
```

Windows PowerShell 参考启动方式：

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PHONE_UPLOAD_TOKEN = "替换为随机长 Token"
$env:PHONE_MCP_DATA_DIR = "$PWD\data"
.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 18770
```

以上命令仅作为移植参考，尚未验证开机自启、休眠恢复、Windows 防火墙及不同隧道工具下的完整行为。长期稳定运行仍建议使用持续在线的 Linux 服务器。

## 鉴权

除 `/health` 外，手机 HTTP 端点和 `/mcp` 都要求：

```text
Authorization: Bearer YOUR_TOKEN
```

Token 位于 `/opt/phone-screen-mcp/.env`。不要把 `.env`、Token、域名、手机截图或 `data/` 目录提交到公开仓库。

在 MCP 客户端中连接 `https://YOUR_DOMAIN/mcp`，并配置同一个 Bearer Token。如果你在反向代理层使用 OAuth，也应保留至少一层可靠鉴权，并根据部署方式调整本项目的 Bearer 鉴权。

## iPhone 快捷指令

所有「获取 URL 内容」动作都添加请求头：

```text
Authorization = Bearer YOUR_TOKEN
```

### 1. 上传 App 事件

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

关闭事件将 `event` 改为 `close`。

### 2. 上传截图

- URL：`https://YOUR_DOMAIN/upload`
- 方法：`POST`
- 请求体：文件，即截图结果

### 3. 上传今日屏幕使用时长

1. 添加 Jomo 动作 `Get Today's Screen Time`；
2. 添加「获取 URL 内容」；
3. URL：`https://YOUR_DOMAIN/screen-time`；
4. 方法：`POST`；
5. 请求体选 JSON，字段名使用 `screen_time`，值选择上一步的 Jomo 输出。

也可以直接把 Jomo 输出作为请求体发送；服务器会保存结构化 JSON 或原始文本。

### 4. 执行锁定命令

创建一个轮询快捷指令：

1. `GET https://YOUR_DOMAIN/command`；
2. 从返回字典中获取 `command`；
3. 如果等于 `lock`，运行 Jomo `Start Session`；
4. 如果等于 `unlock`，运行 Jomo `Stop Session`；
5. 其他值不做处理。

`/command` 是一次性队列：手机成功读取后，该命令会立即被删除。请根据自己的需要设置自动化轮询频率。

## MCP 工具

| 工具 | 作用 |
|---|---|
| `get_latest_screen` | 读取最近上传的截图 |
| `get_phone_status` | 读取当前 App 与最后事件 |
| `get_recent_app_events` | 读取最近的 App 打开/关闭记录 |
| `get_today_screen_time` | 读取最近上传的今日屏幕时长 |
| `lock_phone_apps` | 下发整组锁定命令 |
| `unlock_phone_apps` | 下发整组解锁命令 |
| `get_phone_control_status` | 查看是否有待执行命令 |

## 本地测试

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tests/test_server.py
```

## 安全说明

- 使用至少 32 字节的随机 Token，并定期轮换；
- 公网入口必须使用 HTTPS；
- 仅绑定 `127.0.0.1`，由反向代理或隧道负责公网连接；
- 截图和 App 活动属于敏感数据，服务器和备份应启用严格访问控制；
- 开源前再次检查 Git 历史中是否出现 Token、域名、IP、截图或个人姓名。

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
