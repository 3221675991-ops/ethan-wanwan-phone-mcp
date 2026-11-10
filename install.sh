#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

source_dir="$(cd "$(dirname "$0")" && pwd)"
install_dir="/opt/phone-screen-mcp"
service_file="/etc/systemd/system/phone-screen-mcp.service"

if ! id phone-mcp >/dev/null 2>&1; then
  useradd --system --home "$install_dir" --shell /usr/sbin/nologin phone-mcp
fi

install -d -o phone-mcp -g phone-mcp -m 0750 "$install_dir" "$install_dir/data"
install -o phone-mcp -g phone-mcp -m 0644 "$source_dir/server.py" "$install_dir/server.py"
install -o phone-mcp -g phone-mcp -m 0644 "$source_dir/requirements.txt" "$install_dir/requirements.txt"

python3 -m venv "$install_dir/.venv"
"$install_dir/.venv/bin/pip" install --upgrade pip
"$install_dir/.venv/bin/pip" install -r "$install_dir/requirements.txt"

if [[ ! -f "$install_dir/.env" ]]; then
  token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  sed "s|replace-with-a-long-random-token|$token|" "$source_dir/.env.example" \
    > "$install_dir/.env"
  chown phone-mcp:phone-mcp "$install_dir/.env"
  chmod 0600 "$install_dir/.env"
fi

install -o root -g root -m 0644 \
  "$source_dir/phone-screen-mcp.service.example" "$service_file"
systemctl daemon-reload
systemctl enable --now phone-screen-mcp.service

echo "Installed Phone Screen MCP."
echo "Token: $(sed -n 's/^PHONE_UPLOAD_TOKEN=//p' "$install_dir/.env")"
echo "Keep this token private."
