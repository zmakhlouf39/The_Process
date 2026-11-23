#!/usr/bin/env bash
# Adds Grove to your app menu (and optionally starts it at login).
set -e
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
mkdir -p ~/.local/bin ~/.local/share/applications ~/Books
ln -sf "$HERE/grove.sh" ~/.local/bin/grove

cat > ~/.local/share/applications/grove.desktop <<DESK
[Desktop Entry]
Type=Application
Name=Grove
Comment=Your quiet forest library for books & documents
Exec=$HERE/grove.sh
Icon=accessories-dictionary
Terminal=false
Categories=Office;Viewer;Education;
DESK

echo "🌲 Grove installed."
echo "   • Launch it from your app menu, or run: grove"
echo "   • Put books in ~/Books (any subfolders are fine)"

read -r -p "Run Grove in the background automatically when you log in? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
  mkdir -p ~/.config/systemd/user
  cat > ~/.config/systemd/user/grove.service <<SVC
[Unit]
Description=Grove library

[Service]
ExecStart=/usr/bin/env python3 -m grove --no-browser
WorkingDirectory=$HERE
Restart=on-failure
Nice=10

[Install]
WantedBy=default.target
SVC
  systemctl --user daemon-reload
  systemctl --user enable --now grove.service
  # with the service running, the menu entry just opens the page
  sed -i "s|^Exec=.*|Exec=xdg-open http://localhost:8642|" ~/.local/share/applications/grove.desktop
  echo "   • Running in the background — open http://localhost:8642 any time"
fi
