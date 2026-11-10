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
- 限制服务器、备份和日志访问权限；
- 不提交 `.env`、`data/`、截图或真实活动日志；
- 泄露 Token 后立即轮换并重启服务。
