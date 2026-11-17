# Security Policy

## Supported versions

安全修复仅应用于最新发布版本。

## Reporting a vulnerability

请不要在公开 Issue 中发布 Token、域名、IP、截图、App 活动记录或可复现的未修复漏洞细节。请通过仓库维护者提供的私密联系方式报告，并包含：

- 受影响版本；
- 风险与影响；
- 最小复现步骤；
- 建议修复方式（如有）。

维护者确认修复和披露时间后，再公开讨论漏洞细节。

## Deployment checklist

- 使用随机生成的长 Token；
- 只通过 HTTPS 对外提供服务；
- Uvicorn 继续绑定 `127.0.0.1`；
- `/mcp`、`/upload`、`/event`、`/screen-time`、`/command` 与 `/ack` 必须使用 Bearer Token；
- `/health` 只返回服务与心跳概览，不要在其中加入截图或详细 App 活动；
- 截图请求必须保留 iPhone 端的明确确认步骤。`request_screenshot` 只发送 Bark 通知，不代表用户已经授权；
- 截图上传端点本身以 Bearer Token 作为服务端身份验证。部署者必须保护 Token，并确保 iOS 通知自动化在用户确认后才调用 `/upload`；
- 截图与 App 活动属于敏感数据，限制服务器、备份和日志访问权限；
- 不提交 `.env`、`data/`、真实截图、Bark Key 或 App 活动记录；
- 泄露 Token 后立即轮换并重启服务。
