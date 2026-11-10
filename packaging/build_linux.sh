#!/usr/bin/env bash
# Build the CodeSlice Linux package (AppImage + deb + rpm).
#
# Run this on Linux — a native box, a WSL distro, or CI. It cannot run on
# Windows: PyInstaller must produce a Linux backend bundle, and electron-builder
# only builds Linux targets on a Linux host.
#
#   ./packaging/build_linux.sh            # full build
#   ./packaging/build_linux.sh --skip-backend   # reuse packaging/dist-backend-linux
#
# Artifacts land in desktop/release-linux/.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
DESKTOP_DIR="$REPO_ROOT/desktop"
FRONTEND_DIR="$REPO_ROOT/frontend"
DIST_LINUX="$REPO_ROOT/packaging/dist-backend-linux"

SKIP_BACKEND=0
for arg in "$@"; do
  case "$arg" in
    --skip-backend) SKIP_BACKEND=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$1"; }
ok()   { printf '    [ok]   %s\n' "$1"; }
fail() { printf '    [FAIL] %s\n' "$1" >&2; exit 1; }

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || fail "python3 not found"
command -v node >/dev/null || fail "node not found (Node 20+ required)"
command -v npm >/dev/null || fail "npm not found"

if [ "$SKIP_BACKEND" -eq 0 ]; then
  step "Building the Linux backend bundle (PyInstaller)"
  # A throwaway venv keeps the developer's backend/.venv (a Windows one) intact.
  VENV="$REPO_ROOT/packaging/build/linux-venv"
  rm -rf "$VENV"
  if ! "$PYTHON" -m venv "$VENV" 2>/dev/null; then
    # Debian/Ubuntu ship without ensurepip until python3-venv is installed, and
    # that needs root. Bootstrap pip by hand instead of giving up.
    rm -rf "$VENV"
    "$PYTHON" -m venv --without-pip "$VENV" \
      || fail "cannot create a venv; install python3-venv (sudo apt install python3-venv)"
    curl -fsSL --retry 2 --max-time 120 https://bootstrap.pypa.io/get-pip.py -o "$VENV/get-pip.py" \
      || fail "cannot download get-pip.py"
    "$VENV/bin/python" "$VENV/get-pip.py" --no-warn-script-location >/dev/null \
      || fail "cannot bootstrap pip into the venv"
    rm -f "$VENV/get-pip.py"
    ok "venv created without ensurepip"
  fi
  "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV/bin/python" -m pip install -e "$BACKEND_DIR" -r "$REPO_ROOT/packaging/requirements-packaging.txt" >/dev/null
  ok "dependencies installed"

  rm -rf "$DIST_LINUX" "$REPO_ROOT/packaging/build/backend-linux"
  "$VENV/bin/python" -m PyInstaller \
    --noconfirm --clean --log-level WARN \
    --distpath "$DIST_LINUX" \
    --workpath "$REPO_ROOT/packaging/build/backend-linux" \
    "$REPO_ROOT/packaging/specs/codeslice-backend.spec"
  ok "PyInstaller finished"

  # electron-builder's extraResources expects the flat layout:
  #   dist-backend-linux/codeslice-backend + _internal/
  if [ -d "$DIST_LINUX/codeslice-backend/_internal" ]; then
    mv "$DIST_LINUX/codeslice-backend" "$DIST_LINUX/.flat"
    shopt -s dotglob
    mv "$DIST_LINUX/.flat"/* "$DIST_LINUX"/
    shopt -u dotglob
    rmdir "$DIST_LINUX/.flat"
  fi
  chmod +x "$DIST_LINUX/codeslice-backend"
  ok "flat layout: $DIST_LINUX"

  step "Smoke-testing the bundled backend"
  SMOKE_DATA="$(mktemp -d)"
  set +e
  ( CODESLICE_PORT=0 CODESLICE_HOST=127.0.0.1 CODESLICE_TOKEN= CODESLICE_DATA_DIR="$SMOKE_DATA" \
      "$DIST_LINUX/codeslice-backend" --selftest )
  SELFTEST=$?
  set -e
  rm -rf "$SMOKE_DATA"
  [ "$SELFTEST" -eq 0 ] || fail "backend selftest failed"
  ok "selftest passed"
fi

[ -x "$DIST_LINUX/codeslice-backend" ] || fail "backend bundle missing: $DIST_LINUX/codeslice-backend"

step "Building the renderer"
cd "$FRONTEND_DIR"
[ -d node_modules ] || npm ci
npm run build
ok "frontend/dist ready"

step "Building the Linux package"
cd "$DESKTOP_DIR"
[ -d node_modules ] || npm ci
npm run build          # compiles the Electron main/preload TypeScript
node scripts/copy-renderer.mjs
npx electron-builder --linux --config electron-builder-linux.yml
ok "artifacts in $DESKTOP_DIR/release-linux"

ls -1 "$DESKTOP_DIR"/release-linux/*.AppImage \
      "$DESKTOP_DIR"/release-linux/*.deb \
      "$DESKTOP_DIR"/release-linux/*.rpm 2>/dev/null || true
