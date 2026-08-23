# Security Policy

## Supported Versions

Security fixes are applied to the latest release on `main`.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please report vulnerabilities privately:

- Email: **security@chipix.com**
- Include: a description of the issue, steps to reproduce or a proof of concept, affected components (backend API, Electron shell, frontend, VS Code extension), and any relevant logs.

You can expect an initial response within 7 days. We will coordinate a fix and disclosure timeline with you.

## Scope Notes

- Chipix is designed to run as a **local desktop application** (backend bound to `127.0.0.1` by default). Issues requiring public network exposure or disabled authentication (`CHIPVERIFY_ALLOW_PUBLIC_BIND`, dev auth bypasses) are in scope only if they affect the documented secure configuration.
- The optional cloud control plane (Convex) and desktop auto-update flow are in scope.
- Reports about third-party EDA tool integrations (Slang, Icarus, Verilator, Yosys) should be filed upstream unless the vulnerability is in our integration code.
