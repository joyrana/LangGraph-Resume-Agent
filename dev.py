from __future__ import annotations

import signal
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_CMD = ["uv", "run", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]
FRONTEND_CMD = ["npm", "run", "dev"]


def _stream_output(prefix: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in iter(process.stdout.readline, ""):
        print(f"[{prefix}] {line}", end="")


def main() -> int:
    frontend_dir = ROOT / "frontend"
    if not (ROOT / ".venv").exists():
        print("Missing .venv. Run `uv venv .venv --python 3.11 && uv sync` first.")
        return 1
    if not (frontend_dir / "node_modules").exists():
        print("Missing frontend/node_modules. Run `cd frontend && npm install` first.")
        return 1

    backend = subprocess.Popen(
        BACKEND_CMD,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    frontend = subprocess.Popen(
        FRONTEND_CMD,
        cwd=frontend_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    processes = [backend, frontend]
    threads = [
        threading.Thread(target=_stream_output, args=("backend", backend), daemon=True),
        threading.Thread(target=_stream_output, args=("frontend", frontend), daemon=True),
    ]

    def shutdown(*_args: object) -> None:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("Starting backend on http://localhost:8000")
    print("Starting frontend on http://localhost:5173")
    print("Press Ctrl+C to stop both services.\n")

    for thread in threads:
        thread.start()

    try:
        while True:
            backend_code = backend.poll()
            frontend_code = frontend.poll()

            if backend_code is not None or frontend_code is not None:
                if backend_code is None:
                    backend.terminate()
                if frontend_code is None:
                    frontend.terminate()
                break

            threading.Event().wait(0.5)
    finally:
        shutdown()

    return 0 if backend.returncode in (0, None) and frontend.returncode in (0, None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
