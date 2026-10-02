#!/usr/bin/env python3
"""Restore mounts for this Pi's existing RX3 rootfs; never assemble/replace it.

Run as the host user that owns the rootfs. --check only inspects. Mount
operations use sudo -n. The original USB export remains read-only; local USB2
database/analysis persist.

Settings (see config.env.example). A real environment variable wins over the
file. The file is $RX3_CONFIG if set, otherwise config.env beside this script:
  RX3_ROOTFS    absolute path of the prepared rootfs; if unset, RX3_HOME is
                used and the rootfs defaults to $RX3_HOME/rx3-rootfs
  RX3_USB_UUID  filesystem UUID of the USB drive (required)
  RX3_USB_UID, RX3_USB_GID   owner of the USB mount (default 1000 and 1000)
The USB mount uses vfat options; another filesystem needs different handling.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def parse_env_file(path):
    """Read simple KEY=value lines (shell style, optional quotes, $VAR)."""
    values = {}
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        key, sep, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not sep or not key.isidentifier():
            continue
        if len(value) >= 2 and value[0] in '"\'' and value[-1] == value[0]:
            value = value[1:-1]
        else:
            value = value.split(' #', 1)[0].strip()
        known = {**os.environ, **values}
        values[key] = re.sub(
            r'\$\{(\w+)\}|\$(\w+)',
            lambda m: known.get(m.group(1) or m.group(2), m.group(0)),
            value,
        )
    return values


def load_settings():
    """Return setting(name, default): environment first, then config.env."""
    here = Path(__file__).absolute()
    values = {}
    explicit = os.environ.get('RX3_CONFIG')
    if explicit:
        if not Path(explicit).is_file():
            raise SystemExit(f'RX3_CONFIG file not found: {explicit}')
        values = parse_env_file(Path(explicit))
    else:
        for path in (here.parent / 'config.env', here.resolve().parent / 'config.env'):
            if path.is_file():
                values = parse_env_file(path)
                break
    return lambda name, default=None: os.environ.get(name, values.get(name, default))


def command(*args):
    subprocess.run(args, check=True)


def mounted(path):
    return subprocess.run(['mountpoint', '-q', str(path)]).returncode == 0


def same(source, target):
    try:
        return os.path.samefile(source, target)
    except FileNotFoundError:
        return False


def bind(source, target, readonly, check, errors):
    source, target = Path(source), Path(target)
    if not source.exists():
        errors.append(f'Missing source: {source}')
        return
    if mounted(target):
        if not same(source, target):
            errors.append(f'Unexpected mount at {target}; left unchanged')
            return
    elif check:
        errors.append(f'Missing bind mount: {target}')
        return
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            target.mkdir(exist_ok=True)
        elif not target.exists():
            target.touch()
        command('sudo', '-n', 'mount', '--bind', str(source), str(target))
    if readonly:
        options = subprocess.check_output(['findmnt', '-n', '-o', 'OPTIONS', '-M', str(target)], text=True).strip().split(',')
        if 'ro' not in options:
            if check:
                errors.append(f'Mount is not read-only: {target}')
            else:
                command('sudo', '-n', 'mount', '-o', 'remount,bind,ro', str(target))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Inspect without changing mounts or files')
    args = parser.parse_args()

    setting = load_settings()
    rootfs = setting('RX3_ROOTFS')
    if not rootfs and setting('RX3_HOME'):
        rootfs = str(Path(setting('RX3_HOME')) / 'rx3-rootfs')
    usb_uuid = setting('RX3_USB_UUID')
    usb_uid = setting('RX3_USB_UID', '1000')
    usb_gid = setting('RX3_USB_GID', '1000')
    problems = []
    if not rootfs:
        problems.append('RX3_ROOTFS (or RX3_HOME) is not set')
    if not usb_uuid:
        problems.append('RX3_USB_UUID is not set')
    if not (usb_uid.isdigit() and usb_gid.isdigit()):
        problems.append('RX3_USB_UID and RX3_USB_GID must be numbers')
    if problems:
        raise SystemExit('Invalid settings: ' + '; '.join(problems)
                         + '. Copy config.env.example to config.env and fill it in.')

    root = Path(rootfs)
    errors = []
    for name in ['root/pdj/rbp-pi', 'lib/fbshim.so', 'bin/busybox', 'etc/asound.conf']:
        if not (root / name).is_file():
            errors.append(f'Missing existing runtime file: {root / name}')
    if errors:
        raise SystemExit('\n'.join(errors))
    for name in ['null', 'zero', 'urandom', 'full', 'snd']:
        bind(Path('/dev') / name, root / 'dev' / name, False, args.check, errors)
    # Preserve the firmware-specific fake /proc files; mount only ALSA's subtree.
    bind('/proc/asound', root / 'proc/asound', False, args.check, errors)
    usb = root / 'media/usb1/sda1'
    device = Path('/dev/disk/by-uuid') / usb_uuid
    if mounted(usb):
        actual = subprocess.check_output(['findmnt', '-n', '-o', 'UUID', '-M', str(usb)], text=True).strip()
        if actual != usb_uuid:
            errors.append(f'Unexpected USB filesystem at {usb}; left unchanged')
            device = None
        else:
            options = subprocess.check_output(['findmnt', '-n', '-o', 'OPTIONS', '-M', str(usb)], text=True).strip().split(',')
            if 'ro' not in options:
                errors.append(f'USB1 is not read-only: {usb}; left unchanged')
                device = None
    elif device.exists():
        if args.check:
            errors.append(f'Missing USB1 mount: {usb}')
            device = None
        else:
            existing = subprocess.run(['findmnt', '-J', '-o', 'TARGET,FSROOT,OPTIONS', '-S', 'UUID=' + usb_uuid], capture_output=True, text=True)
            roots = json.loads(existing.stdout).get('filesystems', []) if existing.returncode == 0 else []
            roots = [r for r in roots if r.get('fsroot') == '/' and not Path(r['target']).is_relative_to(root)]
            if roots:
                host = roots[0]
                options = host['options'].split(',')
                if not any(v in options for v in ('utf8', 'utf8=1')):
                    errors.append('Existing USB mount needs UTF-8 filenames; left unchanged')
                    device = None
                else:
                    bind(host['target'], usb, True, False, errors)
            else:
                usb.mkdir(parents=True, exist_ok=True)
                command('sudo', '-n', 'mount', '-t', 'vfat', '-o', f'ro,uid={usb_uid},gid={usb_gid},utf8=1,nosuid,nodev,noexec', str(device), str(usb))
    else:
        print(f'USB {usb_uuid} absent; player can start without media.')
        device = None
    if device is not None and mounted(usb):
        for part in ['Contents', 'Music', 'PIONEER/Artwork']:
            if (usb / part).is_dir():
                bind(usb / part, root / 'media/usb2/sdb1' / part, True, args.check, errors)
        for part in ['PIONEER/rekordbox', 'PIONEER/USBANLZ']:
            if not (root / 'media/usb2/sdb1' / part).is_dir():
                errors.append(f'Missing prepared local library: {part}; run prepare-library-view.sh')
    if errors:
        raise SystemExit('\n'.join(errors))
    print('RX3 runtime mounts ready; local library files preserved.')


if __name__ == '__main__':
    main()