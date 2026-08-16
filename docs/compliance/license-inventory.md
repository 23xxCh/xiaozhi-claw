# 许可证与 SBOM 清单

## 自有与上游代码

- Hensun 控制面、实时网关和网页控制台：本仓库自有实现，正式发布前由公司确定对外许可证与版权声明。
- `firmware/xiaozhi-esp32`：基于小智 ESP32 固件的独立商业分支，上游使用 MIT License；分发固件和源码包时必须保留其版权与许可声明。
- Hensun 原创 60 状态表情代码和公司素材：保留公司版权；不得把第三方示意图误标为公司原创素材。

## 机器可读清单

运行下列命令，从三个锁定来源生成 CycloneDX 1.5 清单：

```powershell
python scripts/generate_sbom.py
```

输出为 `docs/sbom.cdx.json`，来源包括：

- Python 的 `pyproject.toml` 直接运行依赖（当前记录版本约束，不代表精确安装快照）。
- Web 的 `package-lock.json` 完整锁定依赖及其中声明的许可证。
- ESP-IDF 的 `dependencies.lock` 完整锁定组件版本。

SBOM 是依赖追踪基线，不等同于法律审查。正式发货前仍需对未声明许可证的组件、字体、声音、模型与素材逐项确认商业使用和再分发权，并把对应 NOTICE/许可证文本随产品保存。
