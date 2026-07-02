#!/bin/bash
#
# setup_hotspot.sh — turn this Raspberry Pi into a self-contained WiFi hotspot
# (access point) so the laptop running the CV app can join it and stream the
# fiber diameter directly to the Pi, with no router or university network.
#
# It uses NetworkManager (nmcli), which is the default network backend on
# Raspberry Pi OS Bookworm. The hotspot is given a FIXED address (192.168.4.1)
# and runs a small DHCP server for the laptop, so the connection details never
# change and match what the FrED interfaces display.
#
#   SSID:      FrED_Pi
#   Password:  fredfiber123
#   Pi IP:     192.168.4.1   (the laptop connects to this, port 5005)
#
# Usage (run on the Pi):
#     bash setup_hotspot.sh          # create + start the hotspot
#     bash setup_hotspot.sh down     # stop the hotspot (restore normal WiFi)
#     bash setup_hotspot.sh status   # show the hotspot state and Pi IPs
#
# NOTE: while the Pi is a hotspot, its built-in WiFi is used for the access
# point and is NOT connected to the internet. That is expected and is exactly
# what we want for a guaranteed, self-contained link to the laptop.

set -euo pipefail

# --- These MUST match the constants in external_diameter.py ----------------- #
SSID="FrED_Pi"
PASSWORD="fredfiber123"
CON_NAME="FrED_Hotspot"
IFACE="wlan0"
PI_IP="192.168.4.1"
# --------------------------------------------------------------------------- #

ACTION="${1:-up}"

if ! command -v nmcli >/dev/null 2>&1; then
  cat <<'MSG'
ERROR: nmcli (NetworkManager) was not found.

This script needs NetworkManager, the default on Raspberry Pi OS Bookworm.
If you are on an older Raspberry Pi OS, either:
  * Enable NetworkManager:  sudo raspi-config  ->  Advanced Options  ->
    Network Config  ->  NetworkManager, then reboot and re-run this script; or
  * Set up the hotspot with hostapd + dnsmasq manually.
MSG
  exit 1
fi

show_status() {
  printf "\n--- Hotspot status ---\n"
  nmcli -t -f NAME,TYPE,DEVICE connection show --active | grep -i wifi || true
  printf "\nThis Pi's IP address(es):\n"
  hostname -I || true
  printf "\nLaptop should connect to:  %s   (port 5005)\n\n" "$PI_IP"
}

case "$ACTION" in
  down|stop)
    printf "Stopping hotspot '%s'...\n" "$CON_NAME"
    sudo nmcli connection down "$CON_NAME" 2>/dev/null || true
    printf "Hotspot stopped. NetworkManager will reconnect to your normal "
    printf "WiFi if one is configured.\n"
    exit 0
    ;;
  status)
    show_status
    exit 0
    ;;
  up|start|"")
    : # fall through to set up / start the hotspot
    ;;
  *)
    printf "Unknown option '%s'. Use: up | down | status\n" "$ACTION"
    exit 1
    ;;
esac

printf "\n=== Configuring FrED WiFi hotspot ===\n"
printf "SSID: %s    Password: %s    Pi IP: %s\n\n" "$SSID" "$PASSWORD" "$PI_IP"

# Create the connection profile if it does not already exist.
if ! nmcli -t -f NAME connection show | grep -qx "$CON_NAME"; then
  printf "Creating connection profile '%s'...\n" "$CON_NAME"
  sudo nmcli connection add type wifi ifname "$IFACE" con-name "$CON_NAME" \
    autoconnect yes ssid "$SSID"
fi

# (Re)apply all the access-point settings every run so it stays consistent.
sudo nmcli connection modify "$CON_NAME" \
  802-11-wireless.mode ap \
  802-11-wireless.band bg \
  ipv4.method shared \
  ipv4.addresses "${PI_IP}/24" \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "$PASSWORD"

printf "Starting hotspot...\n"
sudo nmcli connection up "$CON_NAME"

show_status

printf "Hotspot is up. On the laptop:\n"
printf "  1) Connect to WiFi '%s' (password '%s').\n" "$SSID" "$PASSWORD"
printf "  2) Open 'FrED Fiber Measure with Streaming v3', set IP %s, port 5005,\n" "$PI_IP"
printf "     then Connect and Start streaming.\n"
printf "  3) On this Pi's interface press 'Start Diameter/Camera Loop' to graph.\n\n"
printf "To stop the hotspot later:  bash setup_hotspot.sh down\n\n"
