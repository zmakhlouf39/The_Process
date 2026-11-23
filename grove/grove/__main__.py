import argparse

from .server import serve


def main():
    ap = argparse.ArgumentParser(prog="grove", description="Grove — a calm forest library for your books & documents")
    ap.add_argument("--port", type=int, default=8642)
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to reach it from your phone on the same wifi")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab on start")
    a = ap.parse_args()
    serve(a.host, a.port, not a.no_browser)


if __name__ == "__main__":
    main()
