import os
import yaml
import subprocess
import time
import shortuuid

CADDY_BIN = "/opt/portal-aio/caddy_manager/caddy"
CADDY_CONFIG = "/etc/Caddyfile"
CERT_PATH = "/etc/instance.crt"
KEY_PATH = "/etc/instance.key"
MAX_RETRIES = 5


def load_config():
    yaml_path = '/etc/portal.yaml'
    if os.path.exists(yaml_path):
        with open(yaml_path, 'r') as file:
            return yaml.safe_load(file)['applications']
    
    apps_string = os.environ.get('PORTAL_CONFIG', '')
    if not apps_string:
        raise ValueError("No configuration found in YAML or environment variable")
    
    apps = {}
    for app_string in apps_string.split('|'):
        hostname, ext_port, int_port, path, name = app_string.split(':', 4)
        
        apps[name] = {
            'hostname': hostname,
            'external_port': int(ext_port),
            'internal_port': int(int_port),
            'open_path': str(path),
            'name': name
        }
    
    yaml_data = {"applications": apps}
    with open(yaml_path, "w") as file:
        yaml.dump(yaml_data, file, default_flow_style=False, sort_keys=False)
    
    return apps


def validate_cert_and_key():
    try:
        subprocess.run(["openssl", "x509", "-in", CERT_PATH, "-noout"], check=True, stderr=subprocess.DEVNULL)
        subprocess.run(["openssl", "rsa", "-in", KEY_PATH, "-check", "-noout"], check=True, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False


def wait_for_valid_certs():
    attempts = 0
    while attempts < MAX_RETRIES:
        if os.path.exists(CERT_PATH) and os.path.exists(KEY_PATH):
            if validate_cert_and_key():
                print("Certificate and key are present and valid.")
                return True
            else:
                print(f"Files are present but invalid, attempt {attempts + 1} of {MAX_RETRIES}.")
        else:
            print(f"Waiting for certificate and key to be present, attempt {attempts + 1} of {MAX_RETRIES}.")
        attempts += 1
        time.sleep(5)
    return False


def is_port_auth_excluded(external_port):
    auth_exclude = os.getenv('AUTH_EXCLUDE', '')
    excluded_ports = [int(port.strip()) for port in auth_exclude.split(',') if port.strip()]
    return external_port in excluded_ports


def generate_caddyfile(config):
    if os.environ.get('ENABLE_HTTPS', 'false').lower() != 'true' or not wait_for_valid_certs():
        enable_https = False
    else:
        enable_https = True

    enable_auth = True if os.environ.get('ENABLE_AUTH', 'true').lower() != 'false' else False
    web_username = os.environ.get('WEB_USERNAME', 'vastai')
    
    # Dual token support - both WEB_PASSWORD and OPEN_BUTTON_TOKEN can work simultaneously
    web_password = os.environ.get('WEB_PASSWORD')
    open_button_token = os.environ.get('OPEN_BUTTON_TOKEN')
    
    if not web_password and not open_button_token:
        web_password = shortuuid.uuid()
        open_button_token = web_password
    elif not web_password:
        web_password = open_button_token
    elif not open_button_token:
        open_button_token = web_password
    
    caddy_identifier = os.environ.get('VAST_CONTAINERLABEL')
    
    # Configurable options
    enable_compression = os.environ.get('CADDY_ENABLE_COMPRESSION', 'true').lower() == 'true'
    flush_interval = os.environ.get('CADDY_FLUSH_INTERVAL', '-1')  # -1 = immediate (good for SSE)

    if enable_https:
        servers_block = 'servers {\n    listener_wrappers {\n        http_redirect\n        tls\n    }\n}'
    else:
        servers_block = ''

    # Build token matchers - if both tokens are the same, use simple query match
    if web_password == open_button_token:
        token_auth_matcher = f'''(token_auth_matcher) {{
        @token_auth {{
            query token={web_password}
        }}
    }}'''
        cookie_matcher = f'''(has_valid_auth_cookie_matcher) {{
        @has_valid_auth_cookie {{
            header_regexp Cookie {caddy_identifier}_auth_token={web_password}
        }}
    }}'''
        bearer_matcher = f'''(has_valid_bearer_token_matcher) {{
        @has_valid_bearer_token {{
            header Authorization "Bearer {web_password}"
        }}
    }}'''
    else:
        token_auth_matcher = f'''(token_auth_matcher) {{
        @token_auth {{
            expression `{{http.request.uri.query.token}} == "{web_password}" || {{http.request.uri.query.token}} == "{open_button_token}"`
        }}
    }}'''
        cookie_matcher = f'''(has_valid_auth_cookie_matcher) {{
        @has_valid_auth_cookie {{
            expression `{{http.request.header.Cookie}}.contains("{caddy_identifier}_auth_token={web_password}") || {{http.request.header.Cookie}}.contains("{caddy_identifier}_auth_token={open_button_token}")`
        }}
    }}'''
        bearer_matcher = f'''(has_valid_bearer_token_matcher) {{
        @has_valid_bearer_token {{
            expression `{{http.request.header.Authorization}} == "Bearer {web_password}" || {{http.request.header.Authorization}} == "Bearer {open_button_token}"`
        }}
    }}'''

    # Compression snippet
    compression_snippet = '''
    (compression) {
        encode zstd gzip
    }
    ''' if enable_compression else ''

    caddyfile = fr'''
    {{
        {servers_block}
    }}

    (noauth_matcher) {{
        @noauth {{
            path /.well-known/acme-challenge/*
            path /.well-known/change-password
            path /manifest.json
            path /manifest.webmanifest
            path /site.webmanifest
            path /.well-known/security.txt
            path /security.txt
            path /health.ico
        }}
    }}

    (healthicon) {{
        route /health.ico {{
            header Content-Type image/x-icon
            header Access-Control-Allow-Origin *
            header Access-Control-Allow-Methods GET, OPTIONS
            header Access-Control-Allow-Headers *
            respond 200 {{
                body "GIF89a\\x01\\x00\\x01\\x00\\x80\\x00\\x00\\xff\\xff\\xff\\x00\\x00\\x00!\\xf9\\x04\\x01\\x00\\x00\\x00\\x00,\\x00\\x00\\x00\\x00\\x01\\x00\\x01\\x00\\x00\\x02\\x02D\\x01\\x00;"
            }}
        }}
    }}

    (real_ip_map) {{
        map {{http.request.header.cf-connecting-ip}} {{real_ip}} {{
            ""     {{remote_host}}
            default {{http.request.header.cf-connecting-ip}}
        }}
    }}

    (forwarded_protocol_map) {{
        map {{http.request.header.cf-visitor}} {{forwarded_protocol}} {{
            ""     {{scheme}}
            default https
        }}
    }}

    {token_auth_matcher}

    {cookie_matcher}

    {bearer_matcher}
    {compression_snippet}
    '''

    for app_name, app_config in config.items():
        external_port = app_config['external_port']
        internal_port = app_config['internal_port']
        hostname = app_config['hostname']

        if external_port == internal_port or not os.environ.get(f"VAST_TCP_PORT_{external_port}"):
            continue

        caddyfile += f":{external_port} {{\n"
        if enable_https:
            caddyfile += f'    tls {CERT_PATH} {KEY_PATH}\n'
        
        if enable_compression:
            caddyfile += '    import compression\n'
        
        caddyfile += '    root * /opt/portal-aio/caddy_manager/public\n\n'
        caddyfile += '    handle_errors 502 {\n'
        caddyfile += '        rewrite * /502.html\n'
        caddyfile += '        file_server\n'
        caddyfile += '    }\n\n'

        if enable_auth and not is_port_auth_excluded(external_port):
            caddyfile += generate_auth_config(caddy_identifier, web_username, web_password, open_button_token, hostname, internal_port, flush_interval)
        else:
            caddyfile += generate_noauth_config(hostname, internal_port, flush_interval)
                                                               
        caddyfile += "}\n\n"

    return caddyfile, web_username, web_password, open_button_token


def get_reverse_proxy_block(hostname, internal_port, flush_interval):
    use_localhost = False
    header_up_localhost = os.environ.get('CADDY_HEADER_UP_LOCALHOST', '')
    if header_up_localhost:
        ports_list = [p.strip() for p in header_up_localhost.split(',')]
        use_localhost = header_up_localhost.lower() == "true" or str(internal_port) in ports_list
    
    if use_localhost:
        host_header = f"header_up Host localhost:{internal_port}"
    else:
        host_header = f"header_up Host {{upstream_hostport}}"
    
    return f'''reverse_proxy {hostname}:{internal_port} {{
            {host_header}
            header_up X-Forwarded-Proto {{forwarded_protocol}}
            header_up X-Real-IP {{real_ip}}
            flush_interval {flush_interval}
        }}'''


def generate_noauth_config(hostname, internal_port, flush_interval):
    return f'''
    import real_ip_map
    import forwarded_protocol_map

    handle {{
        {get_reverse_proxy_block(hostname, internal_port, flush_interval)}
    }}
    '''


def generate_auth_config(caddy_identifier, username, password, open_button_token, hostname, internal_port, flush_interval):
    hashed_password = subprocess.check_output([CADDY_BIN, 'hash-password', '-p', password]).decode().strip()
    proxy_block = get_reverse_proxy_block(hostname, internal_port, flush_interval)
   
    return f'''    
    import noauth_matcher
    import real_ip_map
    import forwarded_protocol_map

    route @noauth {{
        import healthicon

        handle {{
            {proxy_block}
        }}
    }}

    import token_auth_matcher
    import has_valid_auth_cookie_matcher
    import has_valid_bearer_token_matcher

    route @token_auth {{
        header Set-Cookie "{caddy_identifier}_auth_token={open_button_token}; Path=/; Max-Age=604800; HttpOnly; SameSite=lax"
        uri query -token
        redir * {{uri}} 302
    }}

    route @has_valid_auth_cookie {{
        handle {{
            {proxy_block}
        }}
    }}

    route @has_valid_bearer_token {{
        handle {{
            {proxy_block}
        }}
    }}

    route {{
        basic_auth {{
            {username} "{hashed_password}"
        }}
        header Set-Cookie "{caddy_identifier}_auth_token={password}; Path=/; Max-Age=604800; HttpOnly; SameSite=lax"

        handle {{
            {proxy_block}
        }}
    }}
'''


def main():
    try:
        config = load_config()
        caddyfile_content, username, password, open_token = generate_caddyfile(config)
        
        with open('/etc/Caddyfile', 'w') as f:
            f.write(caddyfile_content)
        
        subprocess.run([CADDY_BIN, 'fmt', '--overwrite', CADDY_CONFIG])
        
        print("*****")
        print("*")
        print("*")
        print("* Automatic login is enabled via the 'Open' button")
        if password != open_token:
            print(f"* Your web credentials are: {username} / {password}")
            print(f"* Open button token is also valid: {open_token}")
        else:
            print(f"* Your web credentials are: {username} / {password}")
        print("*")
        print(f"* To make API requests, pass an Authorization header (Authorization: Bearer {password})")
        print("*")
        print("*")
        print("*****")
  
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()