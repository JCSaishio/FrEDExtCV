#!/bin/bash
#
# start_hub.sh - start the FrED hub on the Raspberry Pi 5.
#
# Usage (from inside this folder):
#     bash start_hub.sh               # dashboard + relay (normal use)
#     bash start_hub.sh --plain       # plain log lines instead of the
#                                     # full-screen dashboard (e.g. over SSH
#                                     # inside tmux, or into a file)
#     bash start_hub.sh --no-relay    # monitor only (Hub - direct testing)
#     bash start_hub.sh --sim         # dry run with fake devices (any PC)
#
# It makes sure the FrED_Hub hotspot is up (it normally already is: it starts
# at boot), then runs hub.py. hub.py only needs Python's standard library, so
# there is no virtual environment to activate. Run setup_install.sh once first.

set -e

HUB_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$HUB_DIR"
# shellcheck disable=SC1091
source "$HUB_DIR/hub.conf"

sim=0
for arg in "$@"; do
  [ "$arg" = "--sim" ] && sim=1
done

if [ "$sim" -eq 0 ]; then
  if ! command -v nmcli >/dev/null 2>&1; then
    printf "\nERROR: nmcli not found. Run the installer first: bash setup_install.sh\n\n"
    exit 1
  fi
  if ! nmcli -t -f NAME connection show --active | grep -qx "$CON_NAME"; then
    printf "\nThe FrED_Hub hotspot is not up - starting it...\n"
    bash "$HUB_DIR/hub_hotspot.sh" up
  fi
fi

printf "\nRunning the FrED hub (hub.py)...\n"
exec python3 "$HUB_DIR/hub.py" "$@"
