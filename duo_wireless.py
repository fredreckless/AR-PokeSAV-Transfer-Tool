#!/usr/bin/env python3
"""duo_wireless.py is now duo_events.py (30 October 2026): one events tool, with commands named
after what you want - gift, news, event. This stub keeps the old options working:

    python duo_wireless.py --trace --wonder-card FILE.wc3    ->  python duo_events.py gift --card FILE.wc3
    python duo_wireless.py --mystery-event FILE              ->  python duo_events.py event --event FILE

Keep it beside duo_events.py and maxduo_saves.py.
"""
import sys

import duo_events

if __name__ == "__main__":
    duo_events.main()
