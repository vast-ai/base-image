#!/bin/bash
set -euo pipefail

apt-get install --no-install-recommends -y sox

. /venv/main/bin/activate

cd "${WORKSPACE}"

[[ ! -d Qwen3-TTS ]] && git clone https://huggingface.co/spaces/Qwen/Qwen3-TTS

cd Qwen3-TTS

sed -i '/^import spaces/d; /^@spaces/d' app.py
sed -i '/^\*\*Note\*\*: This demo uses HuggingFace Spaces Zero GPU/{N;s/.*\n.*/Qwen3-TTS [HuggingFace Space](https:\/\/huggingface.co\/spaces\/Qwen\/Qwen3-TTS)/}' app.py

uv pip install -r requirements.txt qwen-tts
uv pip install https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3+cu12torch2.8cxx11abiTRUE-cp312-cp312-linux_x86_64.whl

# Create startup script
cat > /opt/supervisor-scripts/qwen3-tts.sh << 'EOL'
#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Qwen3 TTS"

. /venv/main/bin/activate

echo "Starting Qwen3 TTS"

cd "${WORKSPACE}/Qwen3-TTS"
python app.py
EOL

chmod +x /opt/supervisor-scripts/qwen3-tts.sh

# Generate the supervisor config files
cat > /etc/supervisor/conf.d/qwen3-tts.conf << 'EOL'
[program:qwen3-tts]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/qwen3-tts.sh
autostart=true
autorestart=true
exitcodes=0
startsecs=0
stopasgroup=true
killasgroup=true
stopsignal=TERM
stopwaitsecs=10
# This is necessary for Vast logging to work alongside the Portal logs (Must output to /dev/stdout)
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_events_enabled=true
stdout_logfile_maxbytes=0
stdout_logfile_backups=0
EOL

# Update supervisor to start the new service
supervisorctl reread
supervisorctl update