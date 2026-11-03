#!/usr/bin/env python3
# agentlog - thin CLI entry point.
#
# The implementation lives in the agentlog/ package (agentlog.cli); this file
# stays so existing Startup/GUI shortcuts, the systemd unit and README
# examples (`python agentlog.py ...`) keep working unchanged.

from agentlog.cli import main

if __name__ == "__main__":
    main()
