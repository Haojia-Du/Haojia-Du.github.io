"""Create a source-only distributable using an explicit file allowlist."""
from __future__ import annotations

import hashlib
import base64
import json
import zipfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    files = [root / name for name in ("pyproject.toml", ".gitignore", "打开可视化界面.cmd", "一键安装并启动.cmd")]
    files += sorted((root / "course_notes").glob("*.py"))
    files += sorted(path for path in (root / "course_notes" / "web").rglob("*") if path.is_file() and path.suffix in {".html", ".css", ".js", ".txt", ".woff2"})
    files += sorted((root / "tests").glob("test_*.py"))
    files += [root / "scripts" / "package_release.py"]
    files += [root / "publish" / "README.md", root / "publish" / "index.html"]
    entries = [{"path": str(path.relative_to(root)).replace("\\", "/"),
                "content": base64.b64encode(path.read_bytes()).decode("ascii") if path.suffix == ".woff2" else path.read_text(encoding="utf-8"),
                "encoding": "base64" if path.suffix == ".woff2" else "utf-8"} for path in files]
    entries.append({"path": "README.md", "content": (root / "publish" / "README.md").read_text(encoding="utf-8")})
    output = root / "dist"
    output.mkdir(exist_ok=True)
    archive = output / "yanxue-windows.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for entry in entries:
            content = entry["content"]
            if entry["path"].endswith(".cmd"):
                content = content.replace("\r\n", "\n").replace("\n", "\r\n")
            bundle.writestr("yanxue/" + entry["path"], base64.b64decode(content) if entry.get("encoding") == "base64" else content.encode("utf-8"))
    manifest = {"version": "0.2.0", "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "files": len(entries)}
    (output / "release-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output / "source-files.json").write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size, **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
