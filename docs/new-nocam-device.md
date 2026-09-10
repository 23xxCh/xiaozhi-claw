# 同款 NOCAM 新设备首次烧录

适用于当前 ESP32-S3 N16R8（16MB Flash）、同款屏幕、麦克风与相同接线。
新设备接入前核对芯片、Flash 容量和实际 MAC；不要复用旧设备的 MAC、密钥或整机备份。

## 当前候选

- 固件源码修订：`31f076e`，增加收音预热期间的旧缓冲排空。
- 应用 SHA256：`37266bddff2873e9c3bf6eb385e0eb3f309fe3e3bfdd90ac70cf0c3bbe95fce1`。
- 用户反馈当前样机恢复多轮；尚未完成长稳验收。
- 当前包使用局域网 Bootstrap：`http://192.168.1.20:8000/v1/device/xiaozhi-bootstrap`。
  必须能访问这台开发电脑；这不是脱离电脑运行的云端发布包。

## 首次接入顺序

1. 拔下旧样机，只连接新板的数据 USB；识别串口、ESP32-S3、16MB Flash 和 MAC。
2. 在控制服务登记新设备，板型为 `hensun-nocam-pilot-v1`，生成独立密钥。
   由维护人员处理凭据，用户无需把密钥发到聊天或填入网页。
3. 使用 `scripts/build_factory_release.py` 生成并验证完整发布清单。
   出厂 profile 默认要求云端地址，不能拿局域网构建冒充云端包。
4. 使用 `scripts/factory_flash.ps1`，传入清单、实际 MAC、独立 SecureString 密钥及已核对串口。
   `-ConfirmFactoryReset` 会重置目标板的配网与身份数据，只用于明确选定的新板。
   脚本校验分区和哈希，烧录 bootloader、分区表、OTA 数据、应用、素材，并写入独立身份。
5. 等设备进入配网，手机连接设备热点，打开 `http://192.168.4.1` 配置 2.4GHz Wi-Fi。
6. 手机恢复正常联网，在 Hensun 网页扫描二维码或输入备用码认领，确认真实在线，选择助手。
7. 验证唤醒、连续追问、尾字和停止，再安排长稳测试。

空白新板不能只写 `xiaozhi.bin`。旧板应用升级和新板首次烧录是两种不同流程。
不要使用 `scripts/provision_lan_device.py` 完成本产品流程：该旧开发脚本使用 CAM 板型并自动创建测试账户。
新板尚未连接时，不登记虚构设备，不操作仍连接的旧样机。
