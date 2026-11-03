"""Run the local services in one terminal: the API and the frontend.

The model server (mlx-lm by default) is checked, not started: it runs in its
own environment on port 8000.

Usage (normally via ./dev.sh)::

    ./dev.sh                    # API + UI
    ./dev.sh --debug            # API under debugpy on :5678 (attach from your IDE)
    ./dev.sh --debug --wait     # ...and block startup until a debugger attaches
    ./dev.sh --no-frontend
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API_HOST, API_PORT = "127.0.0.1", int(os.environ.get("API_PORT", "8001"))  # 8000 is the local mlx-lm model server


def _stream_output(prefix: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in iter(process.stdout.readline, ""):
        print(f"[{prefix}] {line}", end="")


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - local dev URL from settings
            return response.status < 400
    except OSError:
        return False


def _check_model_server() -> None:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.llm_provider == "disabled":
        return
    if _reachable(f"{settings.openai_base_url}/models"):
        print(f"Using the model server at {settings.openai_base_url} ({settings.openai_model})")
    else:
        print(
            f"WARNING: no model server at {settings.openai_base_url}; proposals will fail. Start it with:\n"
            f"  mlx_lm.server --model {settings.openai_model} --port 8000"
        )


def _backend_command(debug: bool, wait: bool) -> list[str]:
    uvicorn = ["-m", "uvicorn", "main:app", "--host", API_HOST, "--port", str(API_PORT)]
    if not debug:
        return [sys.executable, *uvicorn]
    port = os.environ.get("DEBUGPY_PORT", "5678")
    debugpy = ["-m", "debugpy", "--listen", f"127.0.0.1:{port}", *(["--wait-for-client"] if wait else [])]
    print(f"debugpy listening on 127.0.0.1:{port}" + (" (waiting for a debugger to attach)" if wait else ""))
    return [sys.executable, "-Xfrozen_modules=off", *debugpy, *uvicorn]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--debug", action="store_true", help="run the API under debugpy (port $DEBUGPY_PORT, default 5678)")
    parser.add_argument("--wait", action="store_true", help="with --debug: wait for a debugger before serving")
    parser.add_argument("--no-frontend", action="store_true", help="do not start the Vite dev server")
    args = parser.parse_args()

    frontend_dir = ROOT / "frontend"
    if not (ROOT / ".venv").exists():
        print("Missing .venv. Run ./setup.sh first.")
        return 1
    if not args.no_frontend and not (frontend_dir / "node_modules").exists():
        print("Missing frontend/node_modules. Run ./setup.sh first.")
        return 1
    if args.debug:
        try:
            import debugpy  # noqa: F401
        except ImportError:
            print("debugpy is not installed. Run ./setup.sh (or `uv sync --extra dev`).")
            return 1

    _check_model_server()
    services: list[tuple[str, list[str], Path]] = [("backend", _backend_command(args.debug, args.wait), ROOT)]
    if not args.no_frontend:
        services.append(("frontend", ["npm", "run", "dev"], frontend_dir))

    processes = [
        (
            name,
            subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1),
        )
        for name, cmd, cwd in services
    ]

    def shutdown(*_args: object) -> None:
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
        for _, process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    names = [name for name, _ in processes]
    print(f"Starting backend on http://localhost:{API_PORT}")
    if "frontend" in names:
        print("Starting frontend on http://localhost:5173")
    print(f"Press Ctrl+C to stop {', '.join(names)}.\n")

    for name, process in processes:
        threading.Thread(target=_stream_output, args=(name, process), daemon=True).start()

    try:
        # Stop everything as soon as any one service exits.
        while all(process.poll() is None for _, process in processes):
            threading.Event().wait(0.5)
    finally:
        shutdown()

    return 0 if all(process.returncode in (0, None, -signal.SIGTERM) for _, process in processes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
