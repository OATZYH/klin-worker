from __future__ import annotations

import os
import re
from pathlib import Path
import tomllib

SEMVER_TAG_RE = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)$")


def read_pyproject_version(repo_root: Path) -> str:
    pyproject_path = repo_root / "pyproject.toml"
    parsed = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    value = str(parsed["project"]["version"]).strip()
    if not value:
        raise ValueError("project.version is empty in pyproject.toml")
    return value


def read_version_file(repo_root: Path) -> str:
    version_path = repo_root / "VERSION"
    value = version_path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("VERSION file is empty")
    return value


def main() -> int:
    tag = os.environ.get("GITHUB_REF_NAME", "").strip()
    if not tag:
        print("Missing GITHUB_REF_NAME")
        return 1

    match = SEMVER_TAG_RE.match(tag)
    if not match:
        print(f"Invalid tag format. Expected semantic version tag in form vX.Y.Z, got: {tag}")
        return 1

    tag_version = match.group("version")
    repo_root = Path(__file__).resolve().parent.parent
    pyproject_version = read_pyproject_version(repo_root)
    version_file = read_version_file(repo_root)

    if tag_version != pyproject_version or tag_version != version_file:
        print(
            "Release version mismatch detected: "
            f"tag={tag_version} pyproject.toml={pyproject_version} VERSION={version_file}"
        )
        return 1

    print(f"Release tag validated: {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
