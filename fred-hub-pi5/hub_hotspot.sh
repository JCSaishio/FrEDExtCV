#!/bin/bash
#
# hub_hotspot.sh - turn this Raspberry Pi 5 into the FrED_Hub WiFi hotspot.
# The laptop and FrED's Pi 4 both join it; the hotspot hands the laptop an
# address by DHCP (192.168.50.10 and up) while FrED keeps its fixed
# 192.168.50.2 (set on FrED by `bash setup_wifi.sh hub`).
#
# Usage (run on the hub Pi 5):
#     bash hub_hotspot.sh          # create / update + start the hotspot
#     bash hub_hotspot.sh down     # stop it (back to your normal WiFi)
#     bash hub_hotspot.sh status   # show the hotspot state
#
# The profile is saved with autoconnect, so the hotspot comes back by itself
# at every boot - start_hub.sh only checks it is up.
#
# Every setting comes from hub.conf (same folder).
#
# NOTE: while it is the hotspot, the Pi 5's WiFi is NOT connected to the
# internet. If the Pi 5 also has an ethernet cable with internet, the hotspot
# shares it: the laptop and FrED then have internet through the hub too.

set -euo pipefail

HUB_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$HUB_DIR/hub.conf"

ACTION="${1:-up}"

if ! command -v nmcli >/dev/null 2>&1; then
  printf "ERROR: nmcli (NetworkManager) was not found.\n"
  printf "Raspberry Pi OS Bookworm or newer uses it by default; on an older\n"
  printf "image enable it with: sudo raspi-config -> Advanced Options ->\n"
  printf "Network Config -> NetworkManager, then reboot.\n"
  exit 1
fi

show_status() {
  printf "\n--- FrED hub hotspot ---\n"
  if nmcli -t -f NAME connection show --active | grep -qx "$CON_NAME"; then
    printf "Hotspot '%s' is UP (WiFi '%s', password '%s').\n" \
      "$CON_NAME" "$HUB_SSID" "$HUB_PASSWORD"
  else
    printf "Hotspot '%s' is DOWN.\n" "$CON_NAME"
  fi
  if command -v iw >/dev/null 2>&1; then
    iw dev "$IFACE" info 2>/dev/null | grep -E "type|channel" || true
  fi
  printf "\nThis Pi's IP address(es): %s\n" "$(hostname -I 2>/dev/null || true)"
  printf "Hub address: %s   FrED (fixed): %s   port %s\n\n" \
    "$HUB_IP" "$FRED_IP" "$LINK_PORT"
}

case "$ACTION" in
  down|stop)
    printf "Stopping hotspot '%s'...\n" "$CON_NAME"
    sudo nmcli connection down "$CON_NAME" 2>/dev/null || true
    printf "Stopped. It starts again at the next boot (or: bash hub_hotspot.sh).\n"
    exit 0
    ;;
  status)
    show_status
    exit 0
    ;;
  up|start|"")
    : # fall through
    ;;
  *)
    printf "Unknown option '%s'. Use: up | down | status\n" "$ACTION"
    exit 1
    ;;
esac

printf "\n=== Configuring the FrED_Hub WiFi hotspot ===\n"
printf "SSID: %s    Password: %s    Hub IP: %s\n" "$HUB_SSID" "$HUB_PASSWORD" "$HUB_IP"

# Warn before dropping a WiFi SSH session (the radio becomes the hotspot).
if [ -n "${SSH_CONNECTION:-}" ]; then
  ssh_ip="$(printf "%s" "$SSH_CONNECTION" | awk '{print $3}')"
  if ip -4 addr show "$IFACE" 2>/dev/null | grep -q "inet $ssh_ip/"; then
    printf "\nWARNING: you are logged in over this Pi's WiFi (%s). Starting the\n" "$ssh_ip"
    printf "hotspot ends that connection. Reconnect afterwards by joining the\n"
    printf "'%s' WiFi and running: ssh <user>@%s\n" "$HUB_SSID" "$HUB_IP"
    printf "Continuing in 5 s (Ctrl+C to cancel)...\n"
    sleep 5
  fi
fi

# The access point does not appear without a WiFi country code (Bookworm).
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_wifi_country "$WIFI_COUNTRY" >/dev/null 2>&1 || true
elif command -v iw >/dev/null 2>&1; then
  sudo iw reg set "$WIFI_COUNTRY" 2>/dev/null || true
fi
sudo rfkill unblock wifi 2>/dev/null || true

# 5 GHz needs an explicit, non-radar channel for an access point.
channel="$CHANNEL"
if [ "$BAND" = "a" ] && [ -z "$channel" ]; then
  channel="36"
fi

if ! nmcli -t -f NAME connection show | grep -qx "$CON_NAME"; then
  printf "Creating connection profile '%s'...\n" "$CON_NAME"
  sudo nmcli connection add type wifi ifname "$IFACE" con-name "$CON_NAME" \
    autoconnect yes ssid "$HUB_SSID"
fi

# (Re)apply every setting on each run so the profile always matches hub.conf.
#   ipv4.method shared ...... the hub runs DHCP (+ DNS, + internet sharing if
#                             it has ethernet) for the laptop. NetworkManager
#                             leaves .2-.9 out of the DHCP range, so FrED's
#                             fixed .2 can never be handed to someone else.
#   proto/pairwise/group .... plain WPA2-AES, which every laptop accepts
#   pmf disable ............. avoids an AP start-up failure seen with the
#                             Pi's WiFi chip on some Raspberry Pi OS versions
sudo nmcli connection modify "$CON_NAME" \
  802-11-wireless.ssid "$HUB_SSID" \
  802-11-wireless.mode ap \
  802-11-wireless.band "$BAND" \
  802-11-wireless.channel "${channel:-0}" \
  ipv4.method shared \
  ipv4.addresses "${HUB_IP}/24" \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "$HUB_PASSWORD" \
  wifi-sec.proto rsn \
  wifi-sec.pairwise ccmp \
  wifi-sec.group ccmp \
  wifi-sec.pmf disable \
  connection.autoconnect yes \
  connection.autoconnect-priority 100

printf "Starting the hotspot...\n"
sudo nmcli connection up "$CON_NAME"

show_status

printf "Hotspot is up. Next:\n"
printf "  * FrED Pi 4:  bash setup_wifi.sh hub     (once; it stays on the hub)\n"
printf "  * Laptop:     join WiFi '%s' (password '%s')\n" "$HUB_SSID" "$HUB_PASSWORD"
printf "  * This Pi:    bash start_hub.sh          (dashboard + relay)\n\n"
