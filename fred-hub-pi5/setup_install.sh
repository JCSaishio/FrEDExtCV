#!/bin/bash
#
# setup_install.sh - one-shot installer for the FrED hub (Raspberry Pi 5).
# Target: Raspberry Pi 5 running Raspberry Pi OS Bookworm or newer
#         (Desktop or Lite, 64-bit). Run it once, with internet.
#
# What it does:
#   1. Synchronizes the system clock (a wrong date makes apt/HTTPS fail).
#   2. Installs the few system tools the hub uses (apt): iw, ping, rfkill.
#      hub.py itself only needs Python's standard library - no venv, no pip.
#   3. Creates the FrED_Hub hotspot and makes it start at every boot
#      (hub_hotspot.sh). This switches the Pi 5's WiFi to hotspot mode, so it
#      is done LAST (after the downloads). Skip it with --no-hotspot.
#   4. Checks that everything the hub uses is available.
#
# Run it from inside this folder:
#     bash setup_install.sh
#     bash setup_install.sh --no-hotspot     # install only, hotspot later
#
# Afterwards start the hub with:
#     bash start_hub.sh

set -euo pipefail

HUB_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$HUB_DIR"
# shellcheck disable=SC1091
source "$HUB_DIR/hub.conf"

make_hotspot=1
for arg in "$@"; do
  [ "$arg" = "--no-hotspot" ] && make_hotspot=0
done

printf "\n=== FrED hub - Raspberry Pi 5 installer ===\n"
printf "Hub folder: %s\n" "$HUB_DIR"

# ---------------------------------------------------------------------------
# 1. Clock sync. The Pi 5 has a clock chip, but it only keeps time while
#    unplugged if a battery is fitted; with a wrong date apt rejects the
#    repository files ("is not valid yet") and HTTPS downloads fail.
# ---------------------------------------------------------------------------
printf "\n[1/4] Synchronizing the system clock...\n"
printf "Clock before sync: %s\n" "$(date)"

clock_synced() {
  timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -qi yes
}

http_header_date() {
  # Plain HTTP on purpose: with a wrong clock HTTPS certificate checks fail,
  # but the HTTP Date header still arrives and is accurate to the second.
  url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fsI --max-time 10 "$url" 2>/dev/null \
      | tr -d '\r' | grep -i '^date:' | head -n1 | cut -d' ' -f2- || true
  elif command -v wget >/dev/null 2>&1; then
    wget -qS -O /dev/null --timeout=10 "$url" 2>&1 \
      | tr -d '\r' | grep -i '^ *date:' | head -n1 | sed 's/^ *[Dd]ate: *//' || true
  fi
}

sync_clock() {
  if command -v timedatectl >/dev/null 2>&1; then
    sudo timedatectl set-ntp true 2>/dev/null || true
    for _ in $(seq 1 20); do
      clock_synced && return 0
      sleep 1
    done
  fi
  for url in http://google.com http://deb.debian.org http://archive.raspberrypi.org; do
    header_date="$(http_header_date "$url")"
    if [ -n "$header_date" ]; then
      sudo date -s "$header_date" >/dev/null 2>&1 && return 0
    fi
  done
  return 1
}

if clock_synced; then
  printf "Clock is already NTP-synchronized.\n"
elif sync_clock; then
  printf "Clock synchronized.\n"
else
  printf "WARNING: could not synchronize the clock (no internet route?).\n"
  printf "         If apt fails below, connect the Pi 5 to the internet\n"
  printf "         (ethernet, or a normal WiFi: 'bash hub_hotspot.sh down')\n"
  printf "         and run this installer again.\n"
fi
printf "Clock now: %s\n" "$(date)"

# ---------------------------------------------------------------------------
# 2. System tools (apt). All small; most are already on Raspberry Pi OS.
# ---------------------------------------------------------------------------
printf "\n[2/4] Installing system tools with apt (sudo may ask for your password)...\n"
sudo apt-get update
sudo apt-get install -y \
  python3 \
  network-manager \
  iw \
  iputils-ping \
  rfkill \
  iproute2

# python3 ........... runs hub.py (standard library only: socket, curses)
# network-manager ... nmcli, which creates the hotspot (default on Bookworm)
# iw ................ WiFi signal / bitrate of each connected device
# iputils-ping ...... the hub's pings to FrED and the laptop
# rfkill ............ unblocks the WiFi radio
# iproute2 .......... `ip`, to match device addresses

# ---------------------------------------------------------------------------
# 3. The hotspot (starts now and at every boot)
# ---------------------------------------------------------------------------
if [ "$make_hotspot" -eq 1 ]; then
  printf "\n[3/4] Creating the FrED_Hub hotspot (it will start at every boot)...\n"
  bash "$HUB_DIR/hub_hotspot.sh" up
else
  printf "\n[3/4] Skipping the hotspot (--no-hotspot). Create it later with:\n"
  printf "      bash hub_hotspot.sh\n"
fi

# ---------------------------------------------------------------------------
# 4. Verify
# ---------------------------------------------------------------------------
printf "\n[4/4] Checking the tools and Python modules the hub uses...\n"
failed=0
for tool in nmcli iw ping ip; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf "  OK   %s\n" "$tool"
  else
    printf "  FAIL %s not found\n" "$tool"
    failed=1
  fi
done
if python3 - <<'PYCHECK'
import importlib, sys
failed = False
for module in ("curses", "socket", "json", "threading", "subprocess"):
    try:
        importlib.import_module(module)
        print(f"  OK   python3 {module}")
    except Exception as exc:                      # noqa: BLE001
        print(f"  FAIL python3 {module} -> {exc}")
        failed = True
sys.exit(1 if failed else 0)
PYCHECK
then
  :
else
  failed=1
fi
if python3 "$HUB_DIR/hub.py" --check; then
  :
else
  failed=1
fi

if [ "$failed" -ne 0 ]; then
  printf "\nSomething is missing (see FAIL above). Fix it and run the installer again.\n\n"
  exit 1
fi

printf "\n=== Done. ===\n"
printf "Start the hub with:   bash start_hub.sh\n"
printf "Then on FrED's Pi 4:  bash setup_wifi.sh hub     (once)\n"
printf "And on the laptop:    join WiFi '%s' (password '%s')\n\n" "$HUB_SSID" "$HUB_PASSWORD"
