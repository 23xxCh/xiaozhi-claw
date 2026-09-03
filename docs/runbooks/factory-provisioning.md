# 工厂烧录与设备身份

## 职责边界

工厂账户只能注册、质检和查询未出厂设备，不能访问模型密钥、用户记忆或 OTA 发布权限。

## 批次流程

1. 在内部后台按批次导入 SN、板型和硬件版本，并勾选二次确认。
2. 后台仅在注册响应中返回一次设备密钥；立即下载 CSV，服务端只保存哈希。
3. 研发只构建并审核一次 `hensun-nocam-pilot-v1` 黄金包；逐台出厂不得重新编译或替换资源。
4. 双击 `scripts/启动非CAM一键出厂.cmd`，导入一次性清单和黄金包。工具会自动激活工位已安装的 ESP-IDF，用 Windows 当前账户的 DPAPI 加密身份队列，再执行烧录、摘要校验、身份写入和 Wi-Fi 清理，不需要先打开开发终端。
5. 工位逐项确认屏幕、麦克风、扬声器、按键、联网和 3 轮真实对话；通过或失败都保留不含密钥的 `FactoryUnitOutcome`，失败身份只允许原 MAC 返修重试。
6. 设备首次联网后显示绑定二维码和 6 位备用码；工厂不得代用户绑定个人账户。

黄金包只构建一次：

```powershell
python .\scripts\build_factory_release.py `
  --build-dir .\firmware\xiaozhi-esp32\build `
  --profile .\scripts\factory_profiles\hensun-nocam-pilot-v1.json `
  --output-dir .\run\factory-releases\hensun-nocam-pilot-v1-2026.09.03 `
  --release-version 2026.09.03
```

构建器要求编译结果使用非 CAM 板型、16MB Flash、小灿唤醒词、横屏九表情、二维码和 Staging Bootstrap 配置，并固化 bootloader、分区表、OTA 数据、应用、合并资源的 SHA-256。逐台脚本只接受这五段，不接受 CAM 的 `model` 或 `emote_gen` 分区，也不会现场构建。

出厂工具会重新识别唯一在线的 CH340，核对 ESP32-S3、16MB Flash 和 MAC，逐段烧录及校验；只清除 `0x9000–0xCFFF` 通用 Wi-Fi NVS，并在 `0x800000` 独立写入唯一身份。烧录中断会把身份与当前 MAC 绑定为可重试状态，不会自动取走下一台身份。

`flash_device_identity.ps1` 仅写入 `0x800000` 的独立身份分区，本身不会清除位于通用 NVS 的 Wi-Fi。CSV 下载后应转入受控生产系统并按企业密钥管理制度销毁，不得通过聊天或普通邮件传递。

客户步骤见 `docs/customer/internal-pilot-quick-start.md`。当前 Staging 只用于内部测试，不能把该说明卡作为公开销售说明书。

## 质检门槛

- SN、板型、硬件版本与烧录清单一致。
- Bootstrap 能鉴权，未认领设备显示 10 分钟有效的绑定二维码和 6 位备用码。
- 麦克风、扬声器、横屏表情、按键、Wi-Fi、3 轮对话和恢复配网均通过；批次首台另做 10 轮情绪联动测试。
- OTA 固件 SHA-256 不匹配时设备必须拒绝升级。
