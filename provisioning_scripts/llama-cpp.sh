#!/bin/bash

set -euo pipefail

cuda_ver="${CUDA_VERSION%.*}"

llama_dir="${WORKSPACE}/llama.cpp"
llama_ver_dir="${WORKSPACE}/llama.cpp/cuda-${cuda_ver}"

echo "PATH=\"${llama_ver_dir}:${PATH}\"" >> /etc/environment
echo "LD_LIBRARY_PATH=\"${llama_ver_dir}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}\"" >> /etc/environment

mkdir -p "${llama_dir}"

default_cache="${HOME}/.cache/llama.cpp"
mkdir -p "${HOME}/.cache"
ln -sf "${llama_dir}" "${default_cache}" || true

if [[ ! -d "${llama_dir}/cuda-${cuda_ver}" ]]; then
  latest_tag=$(curl -s https://api.github.com/repos/ai-dock/llama.cpp-cuda/releases/latest | jq -r '.tag_name')
  package_name="llama.cpp-${latest_tag}-cuda-${cuda_ver}.tar.gz"
  download_url="https://github.com/ai-dock/llama.cpp-cuda/releases/download/${latest_tag}/${package_name}"
  wget -O "${llama_dir}/${package_name}" "${download_url}"
  (cd "$llama_dir" && tar xf "${package_name}")
fi
  

# Create Llama.cpp startup script
cat > /opt/supervisor-scripts/llama.sh << 'EOL'
#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Llama.cpp"

echo "Starting Llama.cpp"

cd "${WORKSPACE}/"
if [[ -n "${LLAMA_MODEL:-}" ]]; then
  llama-server -hf "$LLAMA_MODEL" ${LLAMA_ARGS:-} 2>&1
else
  echo "Model not specified.  Exiting"
  sleep 6
fi
EOL

chmod +x /opt/supervisor-scripts/llama.sh

# Generate the supervisor config files
cat > /etc/supervisor/conf.d/llama.conf << 'EOL'
[program:llama]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/llama.sh
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
