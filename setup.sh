#!/usr/bin/env bash
# Install everything needed to develop and run the app locally. Safe to re-run.
#
#   ./setup.sh                 # tools, system packages, Python + Node deps, .env
#   ./setup.sh --skip-system   # skip LibreOffice/poppler/Node.js installation
#
# The model server (mlx-lm, ...) is not installed here: run your own and point
# OPENAI_BASE_URL at it in .env.
#
# macOS uses Homebrew; Debian/Ubuntu uses apt (sudo). Other platforms: install
# the system packages listed in the README by hand and pass --skip-system.
set -euo pipefail
cd "$(dirname "$0")"

SKIP_SYSTEM=0
for arg in "$@"; do
  case "$arg" in
    --skip-system) SKIP_SYSTEM=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

OS="$(uname -s)"

# ---------------------------------------------------------------- system packages
if [[ $SKIP_SYSTEM -eq 0 ]]; then
  step "System packages (LibreOffice, poppler, Node.js)"
  if [[ "$OS" == "Darwin" ]]; then
    have brew || { echo "Homebrew is required on macOS: https://brew.sh" >&2; exit 1; }
    have soffice || [[ -d /Applications/LibreOffice.app ]] || brew install --cask libreoffice
    have pdftoppm || brew install poppler
    have node || brew install node
  elif have apt-get; then
    pkgs=()
    have soffice || pkgs+=(libreoffice-writer-nogui fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation2)
    have pdftoppm || pkgs+=(poppler-utils)
    have node || pkgs+=(nodejs npm)
    if [[ ${#pkgs[@]} -gt 0 ]]; then
      sudo apt-get update && sudo apt-get install -y --no-install-recommends "${pkgs[@]}"
    fi
  else
    warn "Unsupported platform ($OS): install LibreOffice, poppler and Node.js 18+ manually."
  fi
  # Homebrew's LibreOffice cask does not put soffice on PATH.
  if ! have soffice && [[ -x /Applications/LibreOffice.app/Contents/MacOS/soffice ]]; then
    warn "soffice is not on PATH. Add RESUME_SOFFICE_PATH=/Applications/LibreOffice.app/Contents/MacOS/soffice to .env"
  fi
fi

# ---------------------------------------------------------------- uv + Python
step "Python environment (uv)"
if ! have uv; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv sync --extra dev   # creates .venv with app + dev tools (pytest, ruff, mypy, debugpy)

# ---------------------------------------------------------------- frontend
step "Frontend dependencies (npm)"
have npm || { echo "npm not found. Install Node.js 18+ and re-run." >&2; exit 1; }
node_major="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$node_major" -ge 18 ]] || warn "Node.js $node_major found; 18+ is required."
(cd frontend && { npm ci || npm install; })

# ---------------------------------------------------------------- configuration
step "Configuration"
if [[ -f .env ]]; then
  echo ".env exists; leaving it unchanged."
else
  cp env.example .env
  if ! have soffice && [[ -x /Applications/LibreOffice.app/Contents/MacOS/soffice ]]; then
    echo "RESUME_SOFFICE_PATH=/Applications/LibreOffice.app/Contents/MacOS/soffice" >> .env
  fi
  echo "Created .env from env.example."
fi

step "Done"
cat <<'EOF'
  ./dev.sh                 start API (:8001) + UI (:5173)
  ./dev.sh --debug         ...with the API under debugpy on :5678
  uv run pytest            run the test suite
EOF
