import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("FB_PREFS", os.path.join(tempfile.mkdtemp(prefix="flowbench-test-"), "preferences.json"))
