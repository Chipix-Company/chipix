"""PyInstaller hook: bundle verification services (lazy-imported from routes)."""
hiddenimports = [
    "services.verification.uvm_gen",
    "services.verification.test_plan",
    "services.verification.formal_gen",
    "services.verification.uvm_planner",
    "services.verification.uvm_validator",
    "services.verification.uvm_human_parity",
    "services.verification.testbench_gen",
    "services.verification.result_parser",
    "services.verification.patch_service",
    "services.verification.compile_gate",
]
