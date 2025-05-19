#!/bin/bash

USER_NAME="user"
USER_HOME="/home/$USER_NAME"

# Execute the rest of the commands as the specified user
bash << 'EOF'
cd /tmp
[[ -n $PINOKIO_VERSION ]] || PINOKIO_VERSION=$(curl -s https://api.github.com/repos/pinokiocomputer/pinokio/releases/latest |jq -r .tag_name)
VERSION_NUMBER=${PINOKIO_VERSION#v}
FILE_NAME="pinokio_${VERSION_NUMBER}_amd64.AppImage"
wget -O ${FILE_NAME} https://github.com/pinokiocomputer/pinokio/releases/download/${VERSION_NUMBER}/Pinokio-${VERSION_NUMBER}.AppImage
chmod +x $FILE_NAME
./"$FILE_NAME" --appimage-extract
mv /tmp/squashfs-root /opt/pinokio
chown -R $USER_NAME:$USER_NAME /opt/pinokio
rm -f /tmp/pinokio*
EOF

# Create the desktop file as the user
sudo -u $USER_NAME bash -c "cat > $USER_HOME/Desktop/Pinokio.desktop << 'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=Pinokio
Comment=Pinokio Application
Exec=env HOME=$WORKSPACE APPDIR=/opt/pinokio /opt/pinokio/AppRun --no-sandbox %U
Icon=/opt/pinokio/pinokio.png
Terminal=false
Categories=Utility;
StartupNotify=true
EOF"

# Make the desktop file executable as the user
sudo -u $USER_NAME chmod +x $USER_HOME/Desktop/Pinokio.desktop