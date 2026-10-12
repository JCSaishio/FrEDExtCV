#!/bin/bash
#
# setup_wifi.sh - choose how this FrED Pi 4 is reached by the laptop.
#
#   bash setup_wifi.sh hub       join the hub Pi 5's WiFi 'FrED_Hub' with the
#                                fixed address 192.168.50.2 (normal mode)
#   bash setup_wifi.sh hotspot   be the 'FrED_Pi' hotspot itself, the old way
#                                (fallback; runs setup_hotspot.sh)
#   bash setup_wifi.sh status    show which mode is active
#
# Run it once: the choice is saved in NetworkManager and comes back at every
# boot. In hub mode FrED keeps looking for the hub until it appears, so the
# order in which the two Pis are switched on does not matter.
#
# Why a hub: as the hotspot, FrED's Pi 4 also runs the access point, DHCP and
# DNS for the laptop. On the hub, it is a plain WiFi client and the Pi 5 does
# that work. Compare the two with the "Pi load (CPU)" box (README).

set -euo pipefail

# --- These MUST match the hub's hub.conf and laptop_link.py ----------------- #
HUB_SSID="FrED_Hub"
HUB_PASSWORD="fredfiber123"
HUB_IP="192.168.50.1"
FRED_IP="192.168.50.2"
LINK_PORT="5005"
WIFI_COUNTRY="MX"
IFACE="wlan0"
CLIENT_CON="FrED_Hub_Client"     # this Pi's profile for joining the hub
HOTSPOT_CON="FrED_Hotspot"       # the profile setup_hotspot.sh creates
# --------------------------------------------------------------------------- #

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
ACTION="${1:-status}"

if ! command -v nmcli >/dev/null 2>&1; then
  printf "ERROR: nmcli (NetworkManager) was not found. Raspberry Pi OS\n"
  printf "Bookworm uses it by default; on an older image enable it with\n"
  printf "sudo raspi-config -> Advanced Options -> Network Config.\n"
  exit 1
fi

profile_exists() {
  nmcli -t -f NAME connection show | grep -qx "$1"
}

profile_active() {
  nmcli -t -f NAME connection show --active | grep -qx "$1"
}

show_status() {
  printf "\n--- FrED WiFi mode ---\n"
  if profile_active "$CLIENT_CON"; then
    printf "Mode: HUB - joined '%s' as %s\n" "$HUB_SSID" "$FRED_IP"
    printf "Laptop: join '%s', then 'Hub - direct' (%s:%s)\n" \
      "$HUB_SSID" "$FRED_IP" "$LINK_PORT"
    printf "        or 'Hub - relay' (%s:%s, needs start_hub.sh on the hub)\n" \
      "$HUB_IP" "$LINK_PORT"
    if command -v iw >/dev/null 2>&1; then
      iw dev "$IFACE" link 2>/dev/null | grep -E "signal|tx bitrate" || true
    fi
    if ping -c 1 -W 1 "$HUB_IP" >/dev/null 2>&1; then
      printf "Hub %s answers ping.\n" "$HUB_IP"
    else
      printf "Hub %s does NOT answer ping.\n" "$HUB_IP"
    fi
  elif profile_active "$HOTSPOT_CON"; then
    printf "Mode: FrED HOTSPOT - this Pi is 'FrED_Pi' (192.168.4.1)\n"
    printf "Laptop: join 'FrED_Pi', then 'FrED hotspot' (192.168.4.1:%s)\n" "$LINK_PORT"
  elif profile_exists "$CLIENT_CON" && \
       [ "$(nmcli -g connection.autoconnect connection show "$CLIENT_CON" 2>/dev/null)" = "yes" ]; then
    printf "Mode: HUB, but NOT connected - looking for '%s'.\n" "$HUB_SSID"
    printf "Is the hub Pi 5 on? (its hotspot starts at boot; check with\n"
    printf "'bash hub_hotspot.sh status' on the hub)\n"
  else
    printf "Mode: neither the hub nor the FrED hotspot.\n"
    printf "Choose one:  bash setup_wifi.sh hub   |   bash setup_wifi.sh hotspot\n"
  fi
  printf "\nActive connections:\n"
  nmcli -t -f NAME,TYPE,DEVICE connection show --active || true
  printf "\nThis Pi's IP address(es): %s\n\n" "$(hostname -I 2>/dev/null || true)"
}

case "$ACTION" in
  status)
    show_status
    exit 0
    ;;

  hotspot)
    printf "\n=== Switching FrED to its own hotspot (fallback) ===\n"
    if profile_exists "$CLIENT_CON"; then
      sudo nmcli connection modify "$CLIENT_CON" connection.autoconnect no
      sudo nmcli connection down "$CLIENT_CON" 2>/dev/null || true
    fi
    bash "$PROJECT_DIR/setup_hotspot.sh" up
    printf "Back to hub mode later with:  bash setup_wifi.sh hub\n\n"
    exit 0
    ;;

  hub)
    : # below
    ;;

  *)
    printf "Unknown option '%s'. Use: hub | hotspot | status\n" "$ACTION"
    exit 1
    ;;
esac

printf "\n=== Switching FrED to the hub WiFi '%s' ===\n" "$HUB_SSID"
printf "FrED's fixed address on the hub: %s\n" "$FRED_IP"

# Same radio settings as the hotspot needs: a country code, radio unblocked.
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_wifi_country "$WIFI_COUNTRY" >/dev/null 2>&1 || true
fi
sudo rfkill unblock wifi 2>/dev/null || true

# Stop being the hotspot, now and at boot.
if profile_exists "$HOTSPOT_CON"; then
  printf "Turning off FrED's own hotspot '%s' (kept for the fallback)...\n" "$HOTSPOT_CON"
  sudo nmcli connection modify "$HOTSPOT_CON" connection.autoconnect no
  sudo nmcli connection down "$HOTSPOT_CON" 2>/dev/null || true
fi

if ! profile_exists "$CLIENT_CON"; then
  printf "Creating connection profile '%s'...\n" "$CLIENT_CON"
  sudo nmcli connection add type wifi ifname "$IFACE" con-name "$CLIENT_CON" \
    ssid "$HUB_SSID"
fi

# (Re)apply every setting on each run.
#   ipv4 manual ............ the fixed 192.168.50.2 the laptop and the hub use
#                            (the hub keeps .2-.9 out of its DHCP range)
#   gateway / dns .......... the hub: if the hub has ethernet internet, FrED
#                            gets internet through it (git pull, installer)
#   powersave 2 (= off) .... WiFi power saving on a client adds delays of up
#                            to ~100 ms to incoming messages (sync pings!).
#                            As the hotspot this never applied; as a client
#                            it must be switched off.
#   autoconnect-retries 0 .. keep looking for the hub forever (default: give
#                            up after 4 tries until the next boot)
#   autoconnect-priority ... prefer the hub over any other saved WiFi
sudo nmcli connection modify "$CLIENT_CON" \
  802-11-wireless.ssid "$HUB_SSID" \
  802-11-wireless.mode infrastructure \
  802-11-wireless.powersave 2 \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "$HUB_PASSWORD" \
  ipv4.method manual \
  ipv4.addresses "${FRED_IP}/24" \
  ipv4.gateway "$HUB_IP" \
  ipv4.dns "$HUB_IP" \
  connection.autoconnect yes \
  connection.autoconnect-priority 100 \
  connection.autoconnect-retries 0

printf "Joining '%s'...\n" "$HUB_SSID"
if sudo nmcli --wait 20 connection up "$CLIENT_CON"; then
  printf "Joined the hub.\n"
else
  printf "\nCould not join '%s' right now - is the hub Pi 5 on with its\n" "$HUB_SSID"
  printf "hotspot up? Nothing else to do: FrED keeps looking and joins by\n"
  printf "itself as soon as the hub appears (also after a reboot).\n"
fi

show_status
printf "Then start FrED as usual:  bash start_fred.sh\n"
printf "Back to FrED's own hotspot: bash setup_wifi.sh hotspot\n\n"
