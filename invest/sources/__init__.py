"""データ源の登録。

sources/ 内のモジュールのうち、名前が "_" で始まらず base でないものを自動で読み込み、
モジュール変数 SOURCE (Source のインスタンス) を登録する。
新しいデータ源は sources/<名前>.py を置くだけで CLI・スキーマ・status に反映される。
"""

import importlib
import pkgutil

from .. import db
from .base import Source


def all_sources() -> dict[str, Source]:
    found: dict[str, Source] = {}
    for m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if m.name.startswith("_") or m.name == "base":
            continue
        src = getattr(importlib.import_module(f"{__name__}.{m.name}"), "SOURCE", None)
        if not isinstance(src, Source):
            continue
        for other in found.values():
            if src.name == other.name or src.cli == other.cli:
                raise RuntimeError(f"データ源の name / cli が重複しています: {m.name}")
        found[src.cli] = src
    return found


def connect(path=None):
    """登録済みの全データ源のスキーマを作成して DB を開く。"""
    return db.connect(path, [s.schema for s in all_sources().values() if s.schema])
