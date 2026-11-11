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
ARCH="${CODESLICE_ARCH:-x64}"
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

PYTHON="${PYTHON:-python3}"
command -v node >/dev/null || fail "node not found (Node 20+ required)"
command -v npm >/dev/null || fail "npm not found"

if [ "$SKIP_BACKEND" -eq 0 ]; then
  step "Building the Linux backend bundle (PyInstaller, $ARCH)"
  # A throwaway venv keeps the developer's backend/.venv (a Windows one) intact.
  VENV="$REPO_ROOT/packaging/build/linux-venv"
  rm -rf "$VENV"
  if command -v uv >/dev/null 2>&1; then
    # uv's managed CPython is built against an old glibc (2.17 for the GNU
    # x86_64/arm64 builds). That keeps the bundled libpython usable on older
    # distributions instead of inheriting the CI runner's glibc (2.35+), which
    # was the cause of "GLIBC_2.38 not found" on Ubuntu 22.04 / Debian 12.
    UV_PYTHON="${UV_PYTHON:-3.11}"
    uv venv --python "$UV_PYTHON" "$VENV" >/dev/null \
      || fail "uv venv failed (Python $UV_PYTHON)"
    uv pip install --python "$VENV/bin/python" -e "$BACKEND_DIR" \
      -r "$REPO_ROOT/packaging/requirements-packaging.txt" >/dev/null \
      || fail "dependency installation failed"
    ok "dependencies installed with uv CPython $UV_PYTHON"
  else
    command -v "$PYTHON" >/dev/null || fail "python3 not found (or install uv)"
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
  fi

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

  step "Checking bundled glibc requirements"
  if command -v objdump >/dev/null 2>&1; then
    GLIBC_LIMIT="${GLIBC_LIMIT:-2.35}"
    MAX_GLIBC="$(
      find "$DIST_LINUX" -type f \( -name '*.so' -o -name '*.so.*' -o -perm -u+x \) -print0 \
        | xargs -0 -r -n1 sh -c 'objdump -T "$0" 2>/dev/null | grep -o "GLIBC_[0-9][0-9.]*" | sed "s/GLIBC_//" | sort -V | tail -1' \
        | sort -Vu | tail -1
    )"
    if [ -z "$MAX_GLIBC" ]; then
      printf '    [warn] no GLIBC symbol versions found; skipping the audit\n'
    else
      GLIBC_HIGHER="$(printf '%s\n%s\n' "$MAX_GLIBC" "$GLIBC_LIMIT" | sort -V | tail -1)"
      if [ "$GLIBC_HIGHER" = "$GLIBC_LIMIT" ]; then
        ok "maximum required glibc: $MAX_GLIBC (limit $GLIBC_LIMIT)"
      else
        fail "bundle requires glibc $MAX_GLIBC, above the $GLIBC_LIMIT compatibility limit"
      fi
    fi
  else
    printf '    [warn] objdump not found; install binutils to audit glibc symbols\n'
  fi

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

step "Building the Linux package ($ARCH)"
cd "$DESKTOP_DIR"
[ -d node_modules ] || npm ci
npm run build          # compiles the Electron main/preload TypeScript
node scripts/copy-renderer.mjs
npx electron-builder --linux --config electron-builder-linux.yml "--$ARCH"
ok "artifacts in $DESKTOP_DIR/release-linux"

ls -1 "$DESKTOP_DIR"/release-linux/*.AppImage \
      "$DESKTOP_DIR"/release-linux/*.deb \
      "$DESKTOP_DIR"/release-linux/*.rpm 2>/dev/null || true
