# Source this to run Qt (widgets, WebEngine, PDF) without a display on a Linux box without root,
# e.g. the dev container:  source scripts/headless_env.sh && pytest
# .syslibs/root holds Debian libs unpacked with `apt-get download <pkg>` + `dpkg-deb -x` (see README.sv.md).
_R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.syslibs/root"
export LD_LIBRARY_PATH="$_R/usr/lib/x86_64-linux-gnu:$_R/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LIBGL_DRIVERS_PATH="$_R/usr/lib/x86_64-linux-gnu/dri"
export QT_QPA_PLATFORM=offscreen
export QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu"
export QTWEBENGINE_DISABLE_SANDBOX=1
export PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.venv/bin:$PATH"
# Never download the pose model here: the dev box has no internet route to it.
export LOPANALYS_NO_DOWNLOAD=1
