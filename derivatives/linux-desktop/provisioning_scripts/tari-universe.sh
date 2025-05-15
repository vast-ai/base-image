#!/bin/bash

USER_NAME="user"
USER_HOME="/home/$USER_NAME"

# Create the directory as user
mkdir -p /workspace/tari-universe
chown $USER_NAME:$USER_NAME /workspace/tari-universe

# Execute the rest of the commands as the specified user
sudo -u $USER_NAME bash << 'USERSCRIPT'
cd /workspace/tari-universe
TARI_VERSION=$(curl -s https://api.github.com/repos/tari-project/universe/releases | grep -m 1 "tag_name" | cut -d'"' -f4)
VERSION_NUMBER=${TARI_VERSION#v}
FILE_NAME="tari_universe_${VERSION_NUMBER}_amd64.AppImage"
wget -O ${FILE_NAME} https://github.com/tari-project/universe/releases/download/${TARI_VERSION}/${FILE_NAME}
chmod +x $FILE_NAME
./"$FILE_NAME" --appimage-extract
USERSCRIPT

# Create the desktop file as the user
sudo -u $USER_NAME bash -c "cat > $USER_HOME/Desktop/Tari.desktop << 'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=Tari
Comment=Tari Application
Exec=/workspace/tari-universe/squashfs-root/AppRun
Icon=/workspace/tari-universe/squashfs-root/tari_universe.png
Terminal=false
Categories=Utility;
StartupNotify=true
EOF"

# Make the desktop file executable as the user
sudo -u $USER_NAME chmod +x $USER_HOME/Desktop/Tari.desktop