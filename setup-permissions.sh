#!/usr/bin/env bash
set -euo pipefail

TARGET_USER="${SUDO_USER:-$USER}"
echo "Granting TouchMimic input permissions for user: $TARGET_USER"

RULE_CONTENT='KERNEL=="uinput", SUBSYSTEM=="misc", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput", TAG+="uaccess"
KERNEL=="event*", SUBSYSTEM=="input", MODE="0660", GROUP="input", TAG+="uaccess"
'

if [ "$(id -u)" -eq 0 ]; then
    setfacl -m "u:${TARGET_USER}:rw" /dev/uinput /dev/input/event*
    usermod -aG input "${TARGET_USER}" || true
    printf '%s' "${RULE_CONTENT}" > /etc/udev/rules.d/99-touchmimic.rules
    udevadm control --reload-rules || true
else
    pkexec sh -c "setfacl -m u:${TARGET_USER}:rw /dev/uinput /dev/input/event* && usermod -aG input ${TARGET_USER} && printf '%s' '${RULE_CONTENT}' > /etc/udev/rules.d/99-touchmimic.rules && udevadm control --reload-rules"
fi

echo "✅ Permissions granted! You can now use TouchMimic without rebooting."

