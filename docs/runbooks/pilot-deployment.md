# 试点部署手册

## 边界

首版部署两个应用容器，共用一个 MySQL 8 数据库：

- `control-api`：HTTP 控制面、Bootstrap、OTA 与后台 API。
- `realtime-gateway`：设备 WSS、音频流、模型编排和命令投递。

OSS 仅存固件和静态资源；数据库、日志和 OSS 均不得保存原始音频或逐句对话。

## 上线前配置

1. 从 `.env.example` 创建服务器 Secret，不要提交 `.env`。
2. 使用不少于 24 字符的 `JWT_SECRET`、`DEVICE_CREDENTIAL_PEPPER` 和 `MEMORY_MASTER_KEY`。
3. 设置真实 HTTPS/WSS 域名、微信网页应用回调和 CORS 白名单。
4. 设置 Qwen、DeepSeek 与批量降级链路密钥；在后台填写当前服务商费率。
5. 配置 Ed25519 OTA 公钥。私钥只在离线发布环境中使用。
6. 先执行 `alembic upgrade head`，再启动两个应用容器。

## 验证

```powershell
python scripts/dual_process_smoke.py
python scripts/export_openapi.py
python -m pytest backend/tests -q
cd web
npm run lint
npm run build
```

部署后分别检查 `/health/live` 与 `/health/ready`。WSS 必须使用有效 TLS 证书，模型 Key、设备密钥和令牌不得出现在浏览器或日志中。

## 回退

- 数据库迁移前制作可恢复备份，并在副本上演练升级。
- 保留小智官网版固件作为设备回退渠道，但官网版与自有版必须使用不同 OTA 板型/发布环。
- 自有网关异常时先停止新灰度；不要跨板型下发固件。

Docker Desktop 未运行时只能完成 Compose 配置检查，不能把镜像构建记为通过。
