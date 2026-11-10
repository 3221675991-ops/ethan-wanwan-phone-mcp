# Contributing

欢迎提交 Issue 和 Pull Request。

## 开发流程

1. Fork 仓库并创建独立分支；
2. 不要提交任何真实 Token、域名、IP、截图或 App 活动数据；
3. 修改代码后运行测试；
4. 在 Pull Request 中说明行为变化与验证方式。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tests/test_server.py
```

新增手机控制能力时，应优先保证状态可预测。不要把依赖特定用户名、Jomo 模板名称或私人自动化的逻辑加入通用服务端。
