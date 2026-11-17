# Changelog

本项目遵循语义化版本的版本记录方式。

## [2.1.0] - 2026-11-17

### Added
- 为手机轮询、截图上传、App 事件、屏幕时长和 ACK 上报增加心跳记录。
- 新增 `/ack` 接口，支持 `done` / `failed` 执行回执。
- 命令增加唯一 ID、投递次数、执行尝试次数和失败原因。
- 命令在成功 ACK 前保持 pending；重复 ACK 可安全重试。
- 同一时间只允许一条待确认命令，避免覆盖未完成操作。
- 新增 `watchdog_health` MCP 工具和增强版 `/health` 信息。
- 在 `get_phone_status` 与 `get_phone_control_status` 中增加心跳和 ACK 状态。
- 增加 `HEARTBEAT_TIMEOUT` 环境变量。

### Preserved
- 保留 Bark 截图请求流程：`request_screenshot` 只发送通知，不直接截屏，也不写入命令队列。
- 保留用户明确确认后由 iPhone 自动化执行截图上传的流程。
- 保留现有 `/upload`、`/event`、`/screen-time`、MCP 工具名称及数据目录。

### Changed
- `/command` 读取后不再立即删除命令；必须收到匹配 `command_id` 的 `done` ACK 才清除。
- `failed` ACK 会保留命令并允许下一轮重新投递。
- 更新 README，补充截图授权快捷指令、命令 ACK、手机去重、心跳、健康检查、升级步骤与故障排查。

## Earlier releases

Earlier releases introduced the consent-gated Bark screenshot request and the original iPhone/Jomo integration. See the Git history for the implementation timeline.
