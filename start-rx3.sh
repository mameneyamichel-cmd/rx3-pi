#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
CONFIG="$SCRIPT_DIR/config.env"
if [ ! -f "$CONFIG" ]; then
 echo "Missing $CONFIG; copy config.env.example to config.env and uncomment the settings." >&2
 exit 1
fi
# shellcheck disable=SC1090
. "$CONFIG"
: "${RX3_HOME:?}" "${RX3_ROOTFS:?}" "${RX3_UID:?}" "${RX3_GID:?}" "${RX3_GROUPS:?}" "${RX3_TOUCH_DEV:?}"
RX3_HOME=${RX3_HOME%/}
RX3_ROOTFS=${RX3_ROOTFS%/}
export RX3_HOME RX3_ROOTFS
cd "$RX3_HOME"
exec 9>"$RX3_HOME/.rx3-runtime.lock"
flock -x 9
new_player=0
if ! pgrep -x rbp-pi >/dev/null; then
new_player=1
# Recreate volatile device/library mounts after a reboot.
python3 "$(dirname "$0")/prepare-runtime.py"
# Allocate the shared completed-frame file before launching either process.
truncate -s 8196096 "$RX3_ROOTFS/dev/rx3-present-frame"
# Reset the touchscreen's displayed controls to match firmware startup defaults.
python3 - <<'PY_STATE'
import os,struct
with os.fdopen(os.open(os.environ['RX3_ROOTFS']+'/dev/rx3-ui-state',os.O_RDWR|os.O_CREAT,0o600),'r+b') as f:
    f.write(struct.pack('<I6fII',0x52583332,1,.6,0,1,.5,.5,0,1))
PY_STATE
nohup sudo -n chroot --userspec="$RX3_UID:$RX3_GID" --groups="$RX3_GROUPS" "$RX3_ROOTFS" /bin/busybox env LD_PRELOAD=/lib/fbshim.so /root/pdj/rbp-pi -a > "$RX3_HOME/rx3-player.log" 2>&1 < /dev/null 9>&- &
fi
if ! pgrep -x rx3-fb-present >/dev/null; then
 nohup "$RX3_HOME/rx3-fb-present" "$RX3_ROOTFS/dev/fb0" --fullscreen --coherent > "$RX3_HOME/rx3-present.log" 2>&1 < /dev/null 9>&- &
fi
if ! pgrep -f "^(${RX3_HOME}/|./)rx3-touch-bridge /dev/input/" >/dev/null; then
 nohup "$RX3_HOME/rx3-touch-bridge" "$RX3_TOUCH_DEV" "$RX3_ROOTFS/dev/tsc2007_2-0048" --fullscreen > "$RX3_HOME/rx3-touch.log" 2>&1 < /dev/null 9>&- &
fi
# A repeated start also restores missing helper processes.
if ! pgrep -f "^python3 (${RX3_HOME}/)?flx6-rx3.py$" >/dev/null; then
 nohup python3 "$RX3_HOME/flx6-rx3.py" > "$RX3_HOME/rx3-midi.log" 2>&1 < /dev/null 9>&- &
fi
if [ "$new_player" = 0 ]; then
 echo 'RX3 already running; checked display, touch, and MIDI helpers.'
 exit 0
fi
# Storage workers initialize after the display; report the existing read-only USB.
sleep 10
if pgrep -x rbp-pi >/dev/null && mountpoint -q "$RX3_ROOTFS/media/usb1/sda1"; then
 python3 "$RX3_HOME/pi-control.py" mount
fi
if pgrep -x rbp-pi >/dev/null && mountpoint -q "$RX3_ROOTFS/media/usb2/sdb1/Contents"; then
 python3 - <<'PY_USB2'
import os
f=os.open(os.environ['RX3_ROOTFS']+'/proc/udev_usb2',os.O_RDWR|os.O_NONBLOCK)
os.write(f,b'mount /media/usb2/sdb1')
os.close(f)
PY_USB2
fi
