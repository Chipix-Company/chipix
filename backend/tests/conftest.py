import os

# Prevent pytest from importing script-style integration runner with module-level side effects.
collect_ignore = ["test_rtl_designer_integration.py"]

# Local desktop single-user mode for tests that connect without auth tokens.
os.environ.setdefault("CHIPVERIFY_ALLOW_DEV_AUTH", "true")
