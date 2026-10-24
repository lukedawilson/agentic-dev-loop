# Running a browser inside the sandbox

Getting a browser running inside the engine sandbox is a first-class config
surface: declare one value and supply the browser binary and its system
libraries inside the workspace. This page is the full recipe - what the
engine provides, what the consumer supplies, why `--no-sandbox` is the wrong
answer, and how to prove the sandbox actually engaged.

## What the engine provides

- **The seccomp profile.** Set `isolation.seccomp_profile: bundled:chromium-userns`
  and the engine swaps in its packaged profile at load, resolving the sentinel
  to the real host path. The profile is Docker's default seccomp profile plus
  an allowlist for the user-namespace syscalls Chromium's namespace sandbox
  needs (`clone`, `clone3`, `unshare`). It gates `chroot(2)` on exactly
  `CAP_SYS_CHROOT`, and the exec payload re-adds that capability whenever any
  profile is set (the bundled profile requires it; a custom profile is
  expected to follow the same cap-gated pattern).

  ```yaml
  isolation:
    seccomp_profile: bundled:chromium-userns
  ```

  A raw host path to a custom profile keeps working unchanged; Docker
  validates that path at container-create time. An unknown `bundled:<name>`
  fails at load, naming every known bundled profile.

- **Fonts.** The dev-dotnet image ships fontconfig and fonts
  (`fontconfig-config`, `fonts-liberation`) so Chromium's font enumeration
  does not abort.

The engine does **not** vendor Chromium (size); the consumer brings the
binary.

## What the consumer supplies

The browser binary and its system libraries live inside the workspace, where
the sandbox's single workspace mount makes them visible:

- **Install the browser into the workspace.** Use Playwright's
  `playwright-core install chromium` (or an equivalent bare-binary download)
  so the binary and its syslibs land under the workspace rather than the
  host. The consumer repos already do this for their render steps.
- **Network on the browser step only.** A step that launches Chromium to
  fetch live assets declares `network: true`; the sandbox stays network-off
  everywhere else by default.
- **Sandboxed launch, never `--no-sandbox`.** Pass the Playwright sandbox
  flag (`chromium_sandbox=True` in the Python API, `chromiumSandbox: true`
  in Node). Do not pass `--no-sandbox`: it disables the namespace + seccomp
  boundary and turns the renderer into an unsandboxed process. With the
  sandbox enabled, renderers run seccomp-filtered inside the container.
- **Bare-binary runs.** If you launch the binary directly instead of through
  Playwright, point `LD_LIBRARY_PATH` at the directory holding the installed
  system libraries (Playwright manages this for you when it launches).

A minimal Playwright launch:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(chromium_sandbox=True)
    page = browser.new_page()
    page.goto("https://example.com")
    browser.close()
```

## Verifying the sandbox engaged

Run these inside the browser step's container (or a networked command step)
to prove the boundary is real:

```bash
# 1. The user namespace is available (the profile allows unshare).
python -c 'import os; os.unshare(os.CLONE_NEWUSER)'
# exit 0 means unshare(CLONE_NEWUSER) returned 0

# 2. A renderer process is seccomp-filtered and no-new-privs.
grep -E '^(Seccomp|NoNewPrivs):' /proc/<renderer-pid>/status
# expect: Seccomp: 2  and  NoNewPrivs: 1
```

`Seccomp: 2` means seccomp is in filter mode; `NoNewPrivs: 1` means the
process cannot gain privileges. If either check fails, the sandbox is not
engaged - fix the launch options before trusting the step.
