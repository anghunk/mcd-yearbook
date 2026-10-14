#!/usr/bin/env python3
"""无安装运行入口。

让用户在不配置 PYTHONPATH、不 pip install 的情况下也能一条命令跑通：

    python3 yearbook.py            # 演示数据
    python3 yearbook.py --live     # 真实 MCP 数据（需 MCD_MCP_TOKEN）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from yearbook.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
