"""Checkout entry point; command routing lives in cli.py."""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
