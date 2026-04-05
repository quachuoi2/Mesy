#!/usr/bin/env python3
"""Mesy — Multi-target memory sync daemon.

Thin wrapper that delegates to mesy.main.
"""

from mesy.main import main

if __name__ == "__main__":
    main()
