#!/usr/bin/env python3
"""启动入口：python run.py（零第三方依赖，手机/PC 都能直接跑）。"""

from __future__ import annotations

import os
import sys

from agent_api.config import settings
from agent_api.runtime import create_runtime
from agent_api.server import serve

if __name__ == "__main__":
    if sys.version_info < (3, 9):
        raise SystemExit("需要 Python 3.9+，当前是 %s" % sys.version.split()[0])
    os.makedirs(settings.data_dir, exist_ok=True)
    runtime = create_runtime()
    try:
        serve(runtime, settings.host, settings.port)
    except OSError as exc:
        raise SystemExit(f"启动失败（端口 {settings.port} 可能被占用）：{exc}")
