#!/usr/bin/env bash
# Build a CodeSlice macOS DMG (Intel x64 or Apple Silicon arm64).
#
# Run this on macOS — PyInstaller cannot cross-compile the backend, and dmg
# creation needs hdiutil. GitHub Actions runs it on macos-15-intel (x64) and
# macos-15 (arm64).
#
#   ./packaging/build_mac.sh --arch arm64
#   ./packaging/build_mac.sh --arch x64
#   ./packaging/build_mac.sh --arch arm64 --skip-backend
#
# Artifacts land in desktop/release-mac/.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
DESKTOP_DIR="$REPO_ROOT/desktop"
FRONTEND_DIR="$REPO_ROOT/frontend"
DIST_MAC="$REPO_ROOT/packaging/dist-backend-mac"

SKIP_BACKEND=0
ARCH="${CODESLICE_ARCH:-arm64}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --skip-backend) SKIP_BACKEND=1; shift ;;
    --arch)
      ARCH="${2:-}"
      [ -n "$ARCH" ] || { echo "--arch needs x64 or arm64" >&2; exit 2; }
      shift 2
      ;;
    --arch=*)
      ARCH="${1#--arch=}"
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "$ARCH" in
  x64|arm64) ;;
  *) echo "unsupported arch: $ARCH (use x64 or arm64)" >&2; exit 2 ;;
esac

step() { printf '\n==> %s\n' "$1"; }
ok()   { printf '    [ok]   %s\n' "$1"; }
fail() { printf '    [FAIL] %s\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "macOS is required (PyInstaller and hdiutil are platform-specific)"
command -v node >/dev/null || fail "node not found (Node 20+ required)"
command -v npm >/dev/null || fail "npm not found"

# CI has no signing identity. Do not let electron-builder look for one.
export CSC_IDENTITY_AUTO_DISCOVERY="${CSC_IDENTITY_AUTO_DISCOVERY:-false}"

if [ "$SKIP_BACKEND" -eq 0 ]; then
  step "Building the macOS backend bundle (PyInstaller, $ARCH)"
  VENV="$REPO_ROOT/packaging/build/mac-venv"
  rm -rf "$VENV"
  if command -v uv >/dev/null 2>&1; then
    UV_PYTHON="${UV_PYTHON:-3.11}"
    uv venv --python "$UV_PYTHON" "$VENV" >/dev/null \
      || fail "uv venv failed (Python $UV_PYTHON)"
    uv pip install --python "$VENV/bin/python" -e "$BACKEND_DIR" \
      -r "$REPO_ROOT/packaging/requirements-packaging.txt" >/dev/null \
      || fail "dependency installation failed"
    ok "dependencies installed with uv CPython $UV_PYTHON"
  else
    PYTHON="${PYTHON:-python3}"
    command -v "$PYTHON" >/dev/null || fail "python3 not found (or install uv)"
    "$PYTHON" -m venv "$VENV" || fail "cannot create a venv"
    "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
    "$VENV/bin/python" -m pip install -e "$BACKEND_DIR" -r "$REPO_ROOT/packaging/requirements-packaging.txt" >/dev/null
    ok "dependencies installed"
  fi

  rm -rf "$DIST_MAC" "$REPO_ROOT/packaging/build/backend-mac"
  "$VENV/bin/python" -m PyInstaller \
    --noconfirm --clean --log-level WARN \
    --distpath "$DIST_MAC" \
    --workpath "$REPO_ROOT/packaging/build/backend-mac" \
    "$REPO_ROOT/packaging/specs/codeslice-backend.spec"
  ok "PyInstaller finished"

  # electron-builder's extraResources expects the flat layout:
  #   dist-backend-mac/codeslice-backend + _internal/
  if [ -d "$DIST_MAC/codeslice-backend/_internal" ]; then
    mv "$DIST_MAC/codeslice-backend" "$DIST_MAC/.flat"
    shopt -s dotglob
    mv "$DIST_MAC/.flat"/* "$DIST_MAC"/
    shopt -u dotglob
    rmdir "$DIST_MAC/.flat"
  fi
  chmod +x "$DIST_MAC/codeslice-backend"
  ok "flat layout: $DIST_MAC"

  step "Smoke-testing the bundled backend"
  SMOKE_DATA="$(mktemp -d)"
  set +e
  ( CODESLICE_PORT=0 CODESLICE_HOST=127.0.0.1 CODESLICE_TOKEN= CODESLICE_DATA_DIR="$SMOKE_DATA" \
      "$DIST_MAC/codeslice-backend" --selftest )
  SELFTEST=$?
  set -e
  rm -rf "$SMOKE_DATA"
  [ "$SELFTEST" -eq 0 ] || fail "backend selftest failed"
  ok "selftest passed"
fi

[ -x "$DIST_MAC/codeslice-backend" ] || fail "backend bundle missing: $DIST_MAC/codeslice-backend"

step "Building the renderer"
cd "$FRONTEND_DIR"
[ -d node_modules ] || npm ci
npm run build
ok "frontend/dist ready"

step "Building the macOS package ($ARCH)"
cd "$DESKTOP_DIR"
[ -d node_modules ] || npm ci
npm run build
node scripts/copy-renderer.mjs
npx electron-builder --mac --config electron-builder-mac.yml "--$ARCH"
ok "artifacts in $DESKTOP_DIR/release-mac"

ls -1 "$DESKTOP_DIR"/release-mac/*.dmg 2>/dev/null || true
