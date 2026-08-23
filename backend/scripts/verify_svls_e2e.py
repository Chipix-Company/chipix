"""End-to-end svls lint with real binary."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.sv_lint.toolchain import refresh_svls_cache, resolve_svls_binary, svls_available
from services.sv_lint.service import get_svls_lint_service

BAD_SV = """module A(input logic clk)
always_ff @(posedge clk) begin
end
endmodule
"""

GOOD_SV = """module A;

logic a;

always_comb begin
    a = b | c;
end

endmodule
"""

refresh_svls_cache()
print("binary:", resolve_svls_binary())
print("available:", svls_available())

service = get_svls_lint_service()
root = BACKEND_ROOT / "outputs" / "verify_svls_e2e"
bad = service.lint_source(filepath="bad.sv", content=BAD_SV, project_root=root)
good = service.lint_source(filepath="good.sv", content=GOOD_SV, project_root=root)

print("\n--- BAD SV ---")
print("passed:", bad.passed)
print("diagnostics:", [d.to_dict() for d in bad.diagnostics])
print("agent_blocks:", bad.has_agent_blocking_issues)

print("\n--- GOOD SV ---")
print("passed:", good.passed)
print("diagnostics:", [d.to_dict() for d in good.diagnostics])
print("agent_blocks:", good.has_agent_blocking_issues)
