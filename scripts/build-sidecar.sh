#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/build-sidecar.sh [options]

Options:
  --mode <production|dev>      Build mode (default: production)
  --trace                       Enable shell command tracing (useful in dev mode)
  --skip-tauri-copy             Build only; skip copy into Tauri binaries directory
  --target-triple <triple>      Override Rust target triple (example: x86_64-unknown-linux-gnu)
  --app-repo-path <path>        Tauri app path (default: ../klin-app relative to this repo)
  --python <path>               Explicit Python executable
  --python-version <version>    Python version for py launcher fallback (default: 3.13)
  --metadata-file <path>        Metadata JSON output path (default: dist/build-metadata.json)
  -h, --help                    Show this help

Examples:
  ./scripts/build-sidecar.sh --mode production --skip-tauri-copy --target-triple x86_64-unknown-linux-gnu
  ./scripts/build-sidecar.sh --mode dev --trace
EOF
}

MODE="production"
TRACE=0
SKIP_TAURI_COPY=0
TARGET_TRIPLE=""
APP_REPO_PATH="../klin-app"
PYTHON_BIN=""
PYTHON_VERSION="3.13"
METADATA_FILE=""

log() {
  printf '[build-sidecar] %s\n' "$*"
}

debug() {
  if [[ "$MODE" == "dev" ]]; then
    printf '[build-sidecar][dev] %s\n' "$*"
  fi
}

die() {
  printf '[build-sidecar] ERROR: %s\n' "$*" >&2
  exit 1
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

resolve_path() {
  local value="$1"
  if [[ "$value" = /* ]]; then
    printf '%s\n' "$value"
    return
  fi

  local base="$2"
  printf '%s/%s\n' "$base" "$value"
}

binary_ext_for_os() {
  local uname_value
  uname_value="$(uname -s 2>/dev/null || printf 'unknown')"
  case "$uname_value" in
    MINGW*|MSYS*|CYGWIN*) printf '.exe\n' ;;
    *) printf '\n' ;;
  esac
}

sha256_file() {
  local file_path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file_path" | awk '{print $1}'
    return
  fi

  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file_path" | awk '{print $1}'
    return
  fi

  if command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$file_path" | awk '{print $NF}'
    return
  fi

  die "No SHA256 utility found (sha256sum/shasum/openssl)."
}

human_size() {
  local bytes="$1"
  if command -v numfmt >/dev/null 2>&1; then
    numfmt --to=iec --suffix=B "$bytes"
    return
  fi

  printf '%s bytes\n' "$bytes"
}

resolve_target_triple() {
  if [[ -n "$TARGET_TRIPLE" ]]; then
    printf '%s\n' "$TARGET_TRIPLE"
    return
  fi

  if ! command -v rustc >/dev/null 2>&1; then
    die "Target triple not provided and rustc was not found in PATH."
  fi

  local triple
  triple="$(rustc -vV | awk '/^host: / { print $2; exit }')"
  if [[ -z "$triple" ]]; then
    triple="$(rustc --print host-tuple 2>/dev/null || true)"
  fi

  if [[ -z "$triple" ]]; then
    die "Could not resolve host target triple via rustc."
  fi

  printf '%s\n' "$triple"
}

resolve_python_command() {
  if [[ -n "$PYTHON_BIN" ]]; then
    if [[ ! -x "$PYTHON_BIN" ]]; then
      die "Provided python path is not executable: $PYTHON_BIN"
    fi
    PYTHON_CMD=("$PYTHON_BIN")
    return
  fi

  if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    PYTHON_CMD=("$PROJECT_ROOT/.venv/bin/python")
    return
  fi

  if [[ -x "$PROJECT_ROOT/.venv/Scripts/python.exe" ]]; then
    PYTHON_CMD=("$PROJECT_ROOT/.venv/Scripts/python.exe")
    return
  fi

  if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD=("$(command -v python3)")
    return
  fi

  if command -v python >/dev/null 2>&1; then
    PYTHON_CMD=("$(command -v python)")
    return
  fi

  if command -v py >/dev/null 2>&1; then
    PYTHON_CMD=(py "-$PYTHON_VERSION")
    return
  fi

  die "No usable Python interpreter found."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      [[ $# -ge 2 ]] || die "Missing value for --mode"
      MODE="$2"
      shift 2
      ;;
    --trace)
      TRACE=1
      shift
      ;;
    --skip-tauri-copy)
      SKIP_TAURI_COPY=1
      shift
      ;;
    --target-triple|--target)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      TARGET_TRIPLE="$2"
      shift 2
      ;;
    --app-repo-path)
      [[ $# -ge 2 ]] || die "Missing value for --app-repo-path"
      APP_REPO_PATH="$2"
      shift 2
      ;;
    --python)
      [[ $# -ge 2 ]] || die "Missing value for --python"
      PYTHON_BIN="$2"
      shift 2
      ;;
    --python-version)
      [[ $# -ge 2 ]] || die "Missing value for --python-version"
      PYTHON_VERSION="$2"
      shift 2
      ;;
    --metadata-file)
      [[ $# -ge 2 ]] || die "Missing value for --metadata-file"
      METADATA_FILE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

if [[ "$MODE" != "production" && "$MODE" != "dev" ]]; then
  die "Invalid --mode '$MODE'. Use production or dev."
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPEC_PATH="$PROJECT_ROOT/klin-worker.spec"
DIST_DIR="$PROJECT_ROOT/dist"

if [[ ! -f "$SPEC_PATH" ]]; then
  die "PyInstaller spec not found at $SPEC_PATH"
fi

if [[ "$TRACE" -eq 1 ]]; then
  set -x
fi

resolve_python_command

if [[ -z "$METADATA_FILE" ]]; then
  METADATA_FILE="$DIST_DIR/build-metadata.json"
else
  METADATA_FILE="$(resolve_path "$METADATA_FILE" "$PROJECT_ROOT")"
fi

mkdir -p "$DIST_DIR"
mkdir -p "$(dirname "$METADATA_FILE")"

if ! "${PYTHON_CMD[@]}" -m PyInstaller --version >/dev/null 2>&1; then
  die "PyInstaller is not available for the selected Python interpreter."
fi

PYINSTALLER_ARGS=(--noconfirm "$SPEC_PATH")
if [[ "$MODE" == "dev" ]]; then
  PYINSTALLER_ARGS=(--noconfirm --log-level DEBUG "$SPEC_PATH")
fi

debug "Project root: $PROJECT_ROOT"
debug "Spec path: $SPEC_PATH"
debug "Python command: ${PYTHON_CMD[*]}"
log "Running PyInstaller in $MODE mode"
"${PYTHON_CMD[@]}" -m PyInstaller "${PYINSTALLER_ARGS[@]}"

BINARY_EXT="$(binary_ext_for_os)"
BINARY_NAME="klin-worker${BINARY_EXT}"
BINARY_PATH="$DIST_DIR/$BINARY_NAME"

if [[ ! -f "$BINARY_PATH" ]]; then
  die "PyInstaller did not produce expected binary at $BINARY_PATH"
fi

TARGET_FOR_OUTPUT="$TARGET_TRIPLE"
if [[ "$SKIP_TAURI_COPY" -eq 0 || -n "$TARGET_TRIPLE" ]]; then
  TARGET_FOR_OUTPUT="$(resolve_target_triple)"
fi

COPIED_TO=""
if [[ "$SKIP_TAURI_COPY" -eq 0 ]]; then
  TARGET_DIR="$(resolve_path "$APP_REPO_PATH/src-tauri/binaries" "$PROJECT_ROOT")"
  mkdir -p "$TARGET_DIR"

  TARGET_NAME="klin-worker-${TARGET_FOR_OUTPUT}${BINARY_EXT}"
  DEST_PATH="$TARGET_DIR/$TARGET_NAME"
  cp "$BINARY_PATH" "$DEST_PATH"
  COPIED_TO="$DEST_PATH"
  log "Sidecar copied to $DEST_PATH"
else
  log "Skipped Tauri copy; binary available at $BINARY_PATH"
fi

SIZE_BYTES="$(wc -c < "$BINARY_PATH" | tr -d ' ')"
SIZE_HUMAN="$(human_size "$SIZE_BYTES")"
SHA256="$(sha256_file "$BINARY_PATH")"
PYTHON_ACTUAL="$("${PYTHON_CMD[@]}" --version 2>&1 | tr -d '\r')"
PYINSTALLER_VERSION="$("${PYTHON_CMD[@]}" -m PyInstaller --version 2>/dev/null | tr -d '\r')"
BUILD_TIME_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

METADATA_BINARY_PATH="$(json_escape "$BINARY_PATH")"
METADATA_BINARY_NAME="$(json_escape "$BINARY_NAME")"
METADATA_TARGET="$(json_escape "${TARGET_FOR_OUTPUT:-n/a}")"
METADATA_MODE="$(json_escape "$MODE")"
METADATA_SHA="$(json_escape "$SHA256")"
METADATA_PYTHON="$(json_escape "$PYTHON_ACTUAL")"
METADATA_PI="$(json_escape "$PYINSTALLER_VERSION")"
METADATA_TIME="$(json_escape "$BUILD_TIME_UTC")"
METADATA_COPY="$(json_escape "${COPIED_TO:-}")"

cat > "$METADATA_FILE" <<EOF
{
  "mode": "$METADATA_MODE",
  "binary_name": "$METADATA_BINARY_NAME",
  "binary_path": "$METADATA_BINARY_PATH",
  "size_bytes": $SIZE_BYTES,
  "sha256": "$METADATA_SHA",
  "target_triple": "$METADATA_TARGET",
  "python": "$METADATA_PYTHON",
  "pyinstaller": "$METADATA_PI",
  "built_at_utc": "$METADATA_TIME",
  "copied_to": "$METADATA_COPY"
}
EOF

log "Build summary"
log "  Binary: $BINARY_PATH"
log "  Size: $SIZE_HUMAN ($SIZE_BYTES bytes)"
log "  SHA256: $SHA256"
log "  Target: ${TARGET_FOR_OUTPUT:-n/a}"
log "  Metadata: $METADATA_FILE"
