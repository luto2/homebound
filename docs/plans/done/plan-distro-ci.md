# Plan: pytest on other Linux distributions in CI

Status: implemented (2026-11-14) in `.github/workflows/distros.yml`. Deviations from the plan:

- Step 1 (try each image locally) was skipped: the build machine has no podman or docker. The package
  names were checked against the Arch, Debian and Fedora package indexes instead; the openSUSE names
  are unchecked. The first run on master is the real test; fix failures in a follow-up commit.
- The package lists are longer than the first guess: the PySide6 wheels also link against glib, libX11,
  freetype, and brotli and krb5 (for QtNetwork). Mesa is left out; the GL and EGL loaders (libglvnd) are
  enough for offscreen Qt.
- No `design/qwd/` submodule in the checkout: the repository went private on 2026-11-14 and
  `f5933d7` made builds read the committed copy `homebound/theme/tokens.json` instead, the same as in
  `test.yml`.
- uv is copied from `setup-uv` to `/usr/local/bin` so the test user can run it, and the uv steps set
  `HOME` and `UV_CACHE_DIR` for that user.

- **Audience:** the owner and the coding agents that build this plan.
- **Scope:** running the test suite on Fedora, Arch, Debian and openSUSE in GitHub Actions (TODO 25). The AppImage start check and the AUR `makepkg` check on other distros are separate and not part of this plan. The state of every plan is in [`README.md`](../README.md).
- **Kept current:** written against `74263d9` (2026-11-14). Update it when the plan changes or `.github/workflows/test.yml` changes shape.

## Why

`.github/workflows/test.yml` runs pytest on `ubuntu-latest` (Python 3.12 and 3.14) and
`windows-latest` (3.14). Homebound's Linux users are mostly on other distributions, and Arch is one of
the install targets (the AUR package). The suite there may turn up differences that Ubuntu hides:

- the distribution's own Python version (`UV_PYTHON_PREFERENCE: only-system` makes uv use it);
- the system libraries the PySide6 wheel loads at runtime (EGL, GL, xkbcommon, fontconfig, D-Bus);
- the file system layout and the default locale or time zone, which some tests touch through paths
  and dates.

PySide6 wheels bundle Qt itself, so most failures will be missing libraries, not Qt behaviour. This
check is cheap insurance, not a replacement for a run of the real app on each distribution.

## How

GitHub's hosted Linux runners are Ubuntu only. Other distributions run as Docker containers on an
Ubuntu runner through the job's `container:` key. On a public repo the extra minutes cost nothing.

### Distributions

| Image | Why | Package manager |
|---|---|---|
| `archlinux:latest` | rolling release, newest Python; the AUR users | `pacman` |
| `fedora:latest` | the other big desktop distribution, recent Python | `dnf` |
| `debian:stable` | conservative; its Python sits between the 3.12 and 3.14 jobs | `apt-get` |
| `opensuse/tumbleweed` | rolling, different library package names | `zypper` |

Each job prints `python3 --version` so the log shows which version was tested. If an image's Python is
older than 3.12 (the `requires-python` floor in `pyproject.toml`), the job fails at `uv sync`; that is
the right signal, and the fix is to drop or pin that image, not to download a Python.

### Packages per distribution

Each container starts nearly empty. Before `actions/checkout` it needs `git` (otherwise checkout falls
back to a download without `.git`, and `tools/push_gate.py`, which `tests/test_push_gate.py` imports,
runs `git rev-parse` at import), `tar` and `zstd` (for `actions/cache`), `curl`
or `ca-certificates` (for `setup-uv` and the static data download), Python 3, and the Qt runtime
libraries the Ubuntu job installs with `apt-get`. Starting names, to be checked in step 1:

| Image | Packages |
|---|---|
| Arch | `git tar zstd ca-certificates python libglvnd mesa libxkbcommon fontconfig dbus` |
| Fedora | `git tar zstd ca-certificates python3 mesa-libEGL mesa-libGL libxkbcommon fontconfig dbus-libs` |
| Debian | `git tar zstd ca-certificates curl python3 libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3` |
| openSUSE | `git tar zstd ca-certificates python3 libEGL1 libGL1 libxkbcommon0 fontconfig libdbus-1-3` |

The matrix carries one install command per entry (`include:` with an `install:` string), so the job's
steps stay identical across distributions.

### Root in the container

Containers run as root. `tests/test_backup.py` already skips its read-only folder tests for root
(`IS_ROOT`), so the suite passes, but those tests then never run on these distributions. Step 3 runs
pytest as an ordinary user to keep them (owner, 2026-11-14). Fall back to root only if an image can't
do it; say so in the deviations. The Ubuntu job still covers the skipped tests.

## Steps

1. **Try each image locally.** Run `podman run --rm -it -v "$PWD":/src:ro <image>` (or `docker`),
   copy the checkout into the container, install the packages from the table and run
   `uv sync --python python3`, then `uv run python -m homebound.universe.build_static` and
   `QT_QPA_PLATFORM=offscreen uv run pytest -q`. Fix the package lists
   until the suite passes or the failure is a real Homebound bug. Note real bugs as new TODO items; do
   not paper over them in the workflow.
2. **Add the workflow.** Create `.github/workflows/distros.yml` (a separate workflow, see the decisions) with one job
   `pytest-distro` and a matrix over the four images:
   - `runs-on: ubuntu-latest`, `container: ${{ matrix.image }}`, `fail-fast: false`;
   - env `QT_QPA_PLATFORM: offscreen` and `UV_PYTHON_PREFERENCE: only-system`, as in `test.yml`;
   - steps: install packages → `actions/checkout@v4` (no submodule; see the deviations) →
     `git config --global --add safe.directory "$GITHUB_WORKSPACE"` (the checkout is owned by another
     user inside the container) → `astral-sh/setup-uv@v6` → `python3 --version` → the same weekly
     `data/cache` cache as `test.yml` (same key, so the jobs share it) → `uv sync --python python3` →
     build static data → `uv run pytest -q`.
   - No `actions/setup-python`: the point is the distribution's own Python.
   - No doc check and no mypy: they don't depend on the distribution and `test.yml` runs them.
3. **Run as a user** (see "Root in the container"). Add a `tester` user in the install
   step, `chown` the workspace to it, and run the uv and pytest steps through `runuser -u tester --`
   (`su` on images without `runuser`). Point `UV_CACHE_DIR` into the workspace so the user can write it.
4. **Trigger.** Only `push` to `master`, which is how a merge into master arrives:
   `on: push: branches: [master]`. No `pull_request`, no `schedule`, no `workflow_dispatch`. The push
   gate's `ci/<sha>` branches don't match, so the gate never starts or waits for this workflow.
5. **Docs, same commit.**
   - `docs/code-map.md`, section 7: a row "Tests on other Linux distributions" → `.github/workflows/distros.yml`.
   - `README.md`, Development section: the sentence on `test.yml` also names `distros.yml` and the
     four distributions.
   - `tests/CLAUDE.md`: the first paragraph says where the suite runs; add the distributions.
   - This plan: status line, deviations, `git mv` to `done/`, move the row in [`README.md`](../README.md),
     remove TODO 25.
   - No `CHANGELOG.md` entry: users don't see CI.
6. **Check.** Step 1 is the real test, since the workflow only runs on master. After the commit lands
   on master, watch the run with `gh run list --workflow distros.yml --limit 1` and `gh run watch`. If a
   job fails, fix it in a follow-up commit.

## Owner decisions (2026-11-14)

1. **Only on a merge into master**, in its own `distros.yml`. The push gate
   (`tools/push_gate.py`, `CI_WORKFLOW`) waits for `test.yml` only, so pushes aren't slowed. An Arch or
   Tumbleweed update that breaks a library doesn't block unrelated commits either; it shows up as a
   failed run on master.
2. **Distributions:** Arch, Fedora, Debian stable and openSUSE Tumbleweed. Other Ubuntu versions and
   Linux Mint are close enough to the Ubuntu job. No rawhide or testing images.
3. **Normal user if possible** (step 3); root only as a fallback for an image where it doesn't work.

## Effort

2/5 · Sonnet. The workflow is small and the plan pins it down; the work is getting the package names
right in step 1.
