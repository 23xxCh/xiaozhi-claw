# Hensun shared contracts

The JSON files under `device-config/v1` and `device-ws/v1` are the source of
truth shared by the control plane, ESP32 firmware, and web console.

After changing either source contract, run:

```powershell
python scripts/generate_contracts.py
python scripts/generate_contracts.py --check
python scripts/export_openapi.py
Set-Location web
npm run generate:api
npm run check:api
```

Generated Python, C++, TypeScript, and JSON examples are committed so builds do
not depend on a generator being installed on factory machines. An incompatible
change requires a new contract directory and a version increment; never change
the meaning or range of an existing V1 field silently.
