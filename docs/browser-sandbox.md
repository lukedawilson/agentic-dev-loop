# Running a browser inside the sandbox

Getting a browser running inside the engine sandbox is a first-class config
surface: declare one value, select the browser-ready image, and supply the
browser binary inside the workspace. This page is the full recipe - what the
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

- **The browser-ready image.** The engine publishes `dev-dotnet-browser`
  (`Dockerfile.dotnet-browser`): the dev-dotnet stack plus the chromium
  headless-shell runtime - the font stack (fontconfig, fontconfig-config,
  fonts-liberation, so Skia's font enumeration does not abort) and the
  system shared libraries the binary links against (glib, nss, the X11
  client set, gbm, xkbcommon, asound, atspi, and friends). Published
  multi-arch (amd64 + arm64) by `publish-image.yml` alongside the base
  images, with the digest reported for pinning:

  ```yaml
  isolation:
    image: ghcr.io/lukedawilson/agentic-dev-loop/dev-dotnet-browser@sha256:...
  ```

The engine does **not** vendor Chromium (size); the consumer brings the
binary.

## What the consumer supplies

The browser binary and its system libraries live inside the workspace, where
the sandbox's single workspace mount makes them visible:

- **Install the browser into the workspace.** Use Playwright's
  `playwright-core install chromium` (or an equivalent bare-binary download)
  so the binary lands under the workspace rather than the host. The consumer
  repos already do this for their render steps, normally in the browser
  step's setup hook so agent turns never fetch packages.

The binary alone is not enough: the chromium headless shell links against a
set of system shared libraries (glib, nss, the X11 client set, gbm,
xkbcommon, asound, atspi, and friends). `playwright-core install` fetches
only the binary - it does not bring those libraries, and they cannot be
installed at runtime: the sandbox runs as the host uid with `--cap-drop
ALL`, and the one-shot provision container runs the same way, so `apt` has
no root and the syslibs have nowhere to go. They therefore live in the
engine's `dev-dotnet-browser` image (the `playwright install-deps chromium`
package set for Debian trixie: t64 variants where they exist, e.g.
`libglib2.0-0t64`, `libasound2t64`, with `libxcomposite1` the unt64
exception); a consumer on a different base builds the same apt set into its
own image. A launch that dies with
`error while loading shared libraries: libglib-2.0.so.0` means the pinned
image is missing this set.
- **Network on the browser step only.** A step that launches Chromium to
  fetch live assets declares `network: true`; the sandbox stays network-off
  everywhere else by default.
- **Sandboxed launch, never `--no-sandbox`.** Pass the Playwright sandbox
  flag (`chromium_sandbox=True` in the Python API, `chromiumSandbox: true`
  in Node). Do not pass `--no-sandbox`: it disables the namespace + seccomp
  boundary and turns the renderer into an unsandboxed process. With the
  sandbox enabled, renderers run seccomp-filtered inside the container.
- **Bare-binary runs.** The image carries the libraries at system paths, so
  launching the binary directly needs no `LD_LIBRARY_PATH` juggling
  (Playwright also manages this for you when it launches the binary).

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
