#!/bin/bash

if ! grep -q "### Entrypoint setup ###" /root/.bashrc > /dev/null 2>&1; then
    target_bashrc="/root/.bashrc /home/user/.bashrc"
    echo "### Entrypoint setup ###" | tee -a $target_bashrc
    # Put user into the workspace directory (Dynamic - Cannot rely on docker WORKDIR)
    echo 'cd ${WORKSPACE}' | tee -a $target_bashrc
    # Ensure /etc/environment is sourced on login
    [[ "${export_env}" = "true" ]] && { echo 'set -a'; echo '. /etc/environment'; echo '[[ -f "${WORKSPACE}/.env" ]] && . "${WORKSPACE}/.env"'; echo 'set +a'; } | tee -a $target_bashrc
    # Ensure node npm (nvm) are available on login
    echo '. /opt/nvm/nvm.sh' | tee -a $target_bashrc
    # Ensure users are dropped into the venv on login.  Must be after /.launch has updated PS1
    if [[ "${activate_python_environment}" == "true" ]]; then
        echo '[[ ${CONDA_SHLVL:-0} = 0 ]] && . /venv/${ACTIVE_VENV:-main}/bin/activate' | tee -a $target_bashrc
    fi
    # Warn CLI users if the container provisioning is not yet complete. Red >>>
    echo '[[ -f /.provisioning ]] && echo -e "\e[91m>>>\e[0m Instance provisioning is not yet complete.\n\e[91m>>>\e[0m Required software may not be ready.\n\e[91m>>>\e[0m See /var/log/portal/provisioning.log or the Instance Portal web app for progress updates\n\n"' | tee -a $target_bashrc
    echo "### End entrypoint setup ###" | tee -a $target_bashrc
fi