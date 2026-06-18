from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the read-only mainline research web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8012, type=int)
    args = parser.parse_args()
    uvicorn.run("web.main:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
