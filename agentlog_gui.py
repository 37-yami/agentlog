#!/usr/bin/env python3
# agentlog_gui - thin GUI entry point.
#
# The implementation lives in agentlog/gui/app.py; this file stays so the
# existing shortcuts (agentlog-gui.lnk / desktop) keep working unchanged.

from agentlog.gui.app import main

if __name__ == "__main__":
    main()
