# 工厂烧录与设备身份

## 职责边界

工厂账户只能注册、质检和查询未出厂设备，不能访问模型密钥、用户记忆或 OTA 发布权限。

## 批次流程

1. 在内部后台按批次导入 SN、板型和硬件版本，并勾选二次确认。
2. 后台仅在注册响应中返回一次设备密钥；立即下载 CSV，服务端只保存哈希。
3. 使用 `scripts/flash_device_identity.ps1` 将每台设备的 `device_secret` 写入 NVS。
4. 再烧录对应板型的自有网关固件并执行质检。
5. 设备首次联网后显示 6 位绑定码；工厂不得代用户绑定个人账户。

示例：

```powershell
$secret = Read-Host "Device secret" -AsSecureString
.\scripts\flash_device_identity.ps1 -Port COM6 -DeviceSecret $secret
```

写入身份 NVS 会清除原 Wi-Fi 配置，因此必须在客户配网前完成。CSV 下载后应转入受控生产系统并按企业密钥管理制度销毁，不得通过聊天或普通邮件传递。

## 质检门槛

- SN、板型、硬件版本与烧录清单一致。
- Bootstrap 能鉴权，未认领设备只返回 10 分钟有效的 6 位码。
- 摄像头代码保留但云端视觉返回“未启用”。
- 麦克风、扬声器、屏幕、按键、Wi-Fi、10 轮对话和恢复出厂均通过。
- OTA 固件 SHA-256 不匹配时设备必须拒绝升级。
