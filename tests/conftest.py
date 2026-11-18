import os

# Small population, a throwaway database and a monitor that only runs at startup, for every test module.
os.environ.setdefault("WIQAYA_HOUSEHOLDS", "1500")
os.environ["WIQAYA_DB"] = ":memory:"
os.environ["WIQAYA_MONITOR_MINUTES"] = "600"
