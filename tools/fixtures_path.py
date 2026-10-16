"""统一解析离线 fixtures 目录。

公开仓库里跑的是**脱敏版** fixtures（`fixtures/demo/`），业务字段与真实返回
逐字一致，只把账号 ID / 收货地址 / 联系人 / 手机号之类的个人信息换成了占位值。
本地开发时可以用原始返回（`fixtures/raw/`，已被 .gitignore 排除，不会外泄）。

优先级：

    1. 命令行显式传入 `--raw` / `--demo`
    2. 环境变量 `MCD_FIXTURES`（目录名或绝对路径）
    3. 默认 `fixtures/demo`（不存在时退回 `fixtures/raw`）

这样测试脚本和 demo 脚本都不用改代码就能切换数据源。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "fixtures" / "demo"
RAW = ROOT / "fixtures" / "raw"


def fixture_dir() -> Path:
    argv = sys.argv[1:]
    if "--raw" in argv:
        return RAW
    if "--demo" in argv:
        return DEMO

    env = os.environ.get("MCD_FIXTURES", "").strip()
    if env:
        candidate = Path(env)
        return candidate if candidate.is_absolute() else ROOT / "fixtures" / env

    return DEMO if DEMO.is_dir() else RAW


def fixture_path(name: str) -> Path:
    """取某个 fixture 的完整路径（name 不带 .json）。"""
    return fixture_dir() / f"{name}.json"


def label() -> str:
    """数据来源的中文说明，给命令行输出用。"""
    if fixture_dir() == RAW:
        return "离线 fixtures（真实返回原样落盘）"
    return "离线 fixtures（脱敏版，业务字段与真实返回一致）"
