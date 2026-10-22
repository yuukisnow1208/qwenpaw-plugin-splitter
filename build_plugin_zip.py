# -*- coding: utf-8 -*-
"""把 splitter 插件目录打成可直接在 WebUI「安装插件」里上传的 ZIP。

ZIP 结构（官方推荐）：
    splitter/
      plugin.json
      plugin.py
      core.py
      dist/index.js
      README.md
      tests/...

用法：
    python build_plugin_zip.py
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "splitter"
PLUGIN_ID = "splitter"

#: 运行期产物 / 缓存，不打包
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".git"}
EXCLUDE_FILES = {"config.json", ".DS_Store", "Thumbs.db"}


def main() -> int:
    manifest = json.loads((SRC / "plugin.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    out = ROOT / f"{PLUGIN_ID}-{version}.zip"

    files = []
    for path in sorted(SRC.rglob("*")):
        if path.is_dir():
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.suffix == ".pyc" or path.name in EXCLUDE_FILES:
            continue
        files.append(path)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            arcname = Path(PLUGIN_ID) / path.relative_to(SRC)
            zf.write(path, arcname.as_posix())

    print(f"已生成: {out}  ({out.stat().st_size} bytes)")
    print("压缩包内容:")
    with zipfile.ZipFile(out) as zf:
        for info in zf.infolist():
            print(f"  {info.file_size:>7}  {info.filename}")
        bad = zf.testzip()
        print("完整性校验:", "OK" if bad is None else f"损坏 {bad}")

    names = zipfile.ZipFile(out).namelist()
    roots = {n.split("/")[0] for n in names}
    print("顶层条目:", roots, "->", "结构正确" if roots == {PLUGIN_ID} else "结构异常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
