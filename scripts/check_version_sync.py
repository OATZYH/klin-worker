from __future__ import annotations

from pathlib import Path
import tomllib


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
    repo_root = Path(__file__).resolve().parent.parent
    pyproject_version = read_pyproject_version(repo_root)
    version_file = read_version_file(repo_root)

    if pyproject_version != version_file:
        print(
            f"Version mismatch detected: pyproject.toml={pyproject_version} VERSION={version_file}"
        )
        return 1

    print(f"Version sync OK: {pyproject_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
