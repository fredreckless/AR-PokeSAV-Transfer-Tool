"""Where things live, relative to the repository. Imported first by every test script."""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIRMWARE = os.path.join(ROOT, "firmware")
BUILD = os.path.join(FIRMWARE, "build")
for p in (FIRMWARE, HERE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
if os.environ.get("MAXDUO_TOOL_DIR"):            # mutate.py: test a deliberately broken copy of the tool
    sys.path.insert(0, os.environ["MAXDUO_TOOL_DIR"])


class MissingBuild(Exception):
    """A firmware image the test needs isn't built here (it needs Datel's update file, which the
    repository never holds). Test runners report the test as skipped, not failed."""


def build_file(name, required=True):
    """A firmware image made by firmware/build_firmware.py (never stored in the repository)."""
    path = os.path.join(BUILD, name)
    if not os.path.exists(path):
        if not required:
            return None
        raise MissingBuild(f"{name} isn't built here. Build the firmware first: "
                           f"python firmware/build_firmware.py PATH_TO_DATEL_1.06_UPDATE --all")
    return path


def read_build(name, required=True):
    path = build_file(name, required)
    return open(path, "rb").read() if path else None
