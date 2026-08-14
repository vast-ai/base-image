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


def load_config(yaml_path='/etc/portal.yaml'):
    if os.path.exists(yaml_path):
        # A present-but-unusable cache must be treated as ABSENT — fall through and
        # regenerate from PORTAL_CONFIG rather than crashing Caddy's whole startup.
        # Three ways it gets that way: caddy.sh `touch`es the file before any
        # PORTAL_CONFIG exists (`yaml.safe_load('')` is None); the doc parses but has
        # no usable applications map; or the file is truncated/corrupt — this function
        # writes it non-atomically below, so a container killed mid-write leaves
        # exactly that. Say so in the log: silently discarding an operator's file is
        # how "my portal.yaml edit vanished" becomes an unanswerable question.
        try:
            with open(yaml_path, 'r') as file:
                data = yaml.safe_load(file)
        except (OSError, yaml.YAMLError, UnicodeDecodeError) as e:
            print(f"Ignoring unusable {yaml_path} ({type(e).__name__}: {e}); "
                  f"regenerating from PORTAL_CONFIG")
            data = None
        # Require a NON-EMPTY mapping. The isinstance check matters because a list
        # reaches generate_caddyfile() and dies on .items(); the non-empty check
        # matters because an empty map makes Caddy generate a Caddyfile with no front
        # ports at all — verified on a live instance, where port 1111 disappeared and
        # the box lost every external route in, with nothing to restore it. Treating
        # empty as "regenerate from PORTAL_CONFIG" keeps that self-healing.
        if isinstance(data, dict) and isinstance(data.get('applications'), dict) \
                and data['applications']:
            return data['applications']
        if data is not None:
            print(f"Ignoring {yaml_path}: no usable 'applications' map; "
                  f"regenerating from PORTAL_CONFIG")

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
    
    # Persisting the cache is best-effort: we already have a usable config in hand, so
    # a read-only /etc or an unwritable file must not stop Caddy from starting. (The
    # same condition that makes the file unreadable above usually makes it unwritable.)
    yaml_data = {"applications": apps}
    try:
        with open(yaml_path, "w") as file:
            yaml.dump(yaml_data, file, default_flow_style=False, sort_keys=False)
    except OSError as e:
        print(f"Could not write {yaml_path} ({type(e).__name__}: {e}); "
              f"continuing with the config from PORTAL_CONFIG")

    return apps


# The cert-usability predicate shared with 55-tls-cert-gen.sh (which decides
# whether to REGENERATE) and base/27-caddy-tls.sh (which asserts on the result).
# Exit codes: 0 usable, 3 matched-but-expired, 1 unusable. See its own header for
# why the three hand-rolled copies this replaced were each wrong, and why expiry
# is a separate code rather than a boolean.
#
# 3, not 2: bash's own exit status for a syntax error is 2, so a truncated or
# half-written helper exits 2 — and reading 2 as "expired, serve anyway" would
# serve TLS over whatever is on disk, the fail-open ADR 0026 exists to close. We
# only serve on 3 AND only when stderr carries the helper's own "has expired"
# sentinel (below), so a stray 3 without it fails closed too.
CERT_USABLE = "/opt/instance-tools/bin/cert-usable"
CERT_EXPIRED = 3
CERT_EXPIRED_SENTINEL = "has expired"


def _validate_without_helper():
    """The predicate, in-process, for when the helper is not on disk.

    NOT a fourth answer to the question — deliberately the same comparison the
    helper makes (PEM SubjectPublicKeyInfo of each side, no hashing step in
    which an empty result can stop looking empty), minus the expiry check, which
    this caller tolerates anyway.

    It exists because the portal does NOT ship only by `COPY`. release-portal.yml
    publishes `portal-aio` as a tarball and first_boot/10-update-instance-portal.sh
    untars it over /opt/portal-aio on any version mismatch — so a portal release
    lands on already-published derivatives and on customer images built FROM
    older vast bases, none of which have /opt/instance-tools/bin/cert-usable.
    Failing closed there would disable HTTPS on instances whose certificate is
    perfectly fine: an OLDER image is not a BROKEN one, and this is the one place
    that distinction has to be made in code rather than in an ADR.
    """
    def spki(*args):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return ""
        return r.stdout if r.returncode == 0 else ""

    cert_pub = spki("openssl", "x509", "-in", CERT_PATH, "-noout", "-pubkey")
    key_pub = spki("openssl", "pkey", "-in", KEY_PATH, "-pubout")
    return bool(cert_pub) and cert_pub == key_pub


def validate_cert_and_key():
    """Is the instance certificate good enough for Caddy to serve TLS with?

    "Good enough" is not the same question 55-tls-cert-gen.sh asks. An EXPIRED
    but matched pair is a regenerate at boot and a SERVE here, because the only
    fallback available to this function is no TLS at all — the same public port
    in plaintext, carrying the portal auth token in ?token=. An expired
    certificate still encrypts. Nothing repairs certificates outside a boot, so
    treating expiry as fatal would silently downgrade any long-lived instance
    whose cert lapsed the moment supervisor restarted caddy.

    The previous version validated the key with the RSA-ONLY `openssl rsa` entry
    point and its `-check` flag. A valid EC key cannot be loaded by it at all, so
    an operator-supplied EC pair failed here, wait_for_valid_certs() spent
    MAX_RETRIES x 5s on it, and HTTPS was then disabled on a certificate that was
    completely fine. It also never compared the cert to the key, so a mismatched
    pair was accepted and Caddy served a listener nothing could complete a
    handshake with.
    """
    try:
        proc = subprocess.run(
            [CERT_USABLE, CERT_PATH, KEY_PATH],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, timeout=15, text=True,
        )
    except OSError:
        # The population this branch exists for: an OLDER base image that predates
        # /opt/instance-tools/bin/cert-usable but took a newer portal tarball
        # (release-portal.yml publishes portal-aio, first_boot untars it over an
        # older image). Say so — Caddy logs only to stdout, there is no on-instance
        # log file, and on the exact fleet where the fallback runs an operator
        # otherwise sees only "Files are present but invalid" with no hint that the
        # helper was even absent.
        print("cert-usable helper is absent (older base image); validating with "
              "the in-process SPKI comparison instead.")
        return _validate_without_helper()
    except subprocess.SubprocessError as e:
        print(f"cert-usable helper did not complete ({type(e).__name__}); "
              "validating with the in-process SPKI comparison instead.")
        return _validate_without_helper()

    rc = proc.returncode
    # Serve on expiry ONLY when the helper says so explicitly: exit 3 AND its own
    # "has expired" sentinel on stderr. A syntactically broken helper exits 2 (or
    # some other code) with no sentinel and falls through to `rc == 0` — fail
    # closed — rather than being read as expired.
    if rc == CERT_EXPIRED and CERT_EXPIRED_SENTINEL in (proc.stderr or ""):
        # "when certificate generation is enabled": the two states that produce
        # an expired-and-kept pair are generate_tls_cert not true and a missing
        # helper — in both, a bare "restart to fix" would be false advice.
        print("Instance certificate has EXPIRED but still matches its key; "
              "serving TLS with it rather than falling back to plaintext. "
              "Restarting the instance regenerates it when certificate "
              "generation (generate_tls_cert) is enabled.")
        return True
    return rc == 0


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
    
    # Strip characters that are unsafe in cookie values / CEL strings.
    # Done early so every downstream use (CEL matchers, Set-Cookie headers,
    # basic_auth hash, console output) sees the same sanitized value.
    def _sanitize_token(s):
        return s.replace('\r', '').replace('\n', '').replace(';', '')

    web_password = _sanitize_token(web_password)
    open_button_token = _sanitize_token(open_button_token)

    caddy_identifier = os.environ.get('VAST_CONTAINERLABEL')

    # Configurable options
    enable_compression = os.environ.get('CADDY_ENABLE_COMPRESSION', 'true').lower() == 'true'
    flush_interval = os.environ.get('CADDY_FLUSH_INTERVAL', '-1')  # -1 = immediate (good for SSE)

    if enable_https:
        servers_block = 'servers {\n    listener_wrappers {\n        http_redirect\n        tls\n    }\n}'
    else:
        servers_block = ''

    # Escape passwords for use in CEL expression strings (backtick-delimited).
    # Backslashes and double-quotes must be escaped for CEL string literals.
    def cel_escape(s):
        return s.replace('\\', '\\\\').replace('`', '\\`').replace('"', '\\"').replace('\r', '').replace('\n', '')

    cel_web_password = cel_escape(web_password)
    cel_open_button_token = cel_escape(open_button_token)

    # Build token matchers - always use CEL expressions for safety.
    # Simple query/header_regexp matchers break with regex-special chars in passwords (e.g. * + .)
    if web_password == open_button_token:
        token_cel = f'{{http.request.uri.query.token}} == "{cel_web_password}"'
        cookie_cel = f'{{http.request.header.Cookie}}.contains("{caddy_identifier}_auth_token={cel_web_password}")'
        bearer_cel = f'{{http.request.header.Authorization}} == "Bearer {cel_web_password}"'
    else:
        token_cel = f'{{http.request.uri.query.token}} == "{cel_web_password}" || {{http.request.uri.query.token}} == "{cel_open_button_token}"'
        cookie_cel = f'{{http.request.header.Cookie}}.contains("{caddy_identifier}_auth_token={cel_web_password}") || {{http.request.header.Cookie}}.contains("{caddy_identifier}_auth_token={cel_open_button_token}")'
        bearer_cel = f'{{http.request.header.Authorization}} == "Bearer {cel_web_password}" || {{http.request.header.Authorization}} == "Bearer {cel_open_button_token}"'

    # A valid ?token= request is treated as an interactive browser navigation
    # when it asks for HTML (sent on both http and https) or carries a top-level
    # fetch marker (secure-context only). The Open Button probe is axios
    # (Accept: application/json, text/plain, */* — no text/html, no Sec-Fetch),
    # so it never matches here and falls through to the cookie-less token route,
    # which serves content directly instead of relying on a redirect + cookie.
    browser_nav_cel = '{http.request.header.Accept}.contains("text/html") || {http.request.header.Sec-Fetch-Dest} == "document"'

    token_auth_matcher = f'''(token_auth_matcher) {{
        @token_auth {{
            expression `{token_cel}`
        }}
    }}'''
    token_auth_browser_matcher = f'''(token_auth_browser_matcher) {{
        @token_auth_browser {{
            expression `({token_cel}) && ({browser_nav_cel})`
        }}
    }}'''
    cookie_matcher = f'''(has_valid_auth_cookie_matcher) {{
        @has_valid_auth_cookie {{
            expression `{cookie_cel}`
        }}
    }}'''
    bearer_matcher = f'''(has_valid_bearer_token_matcher) {{
        @has_valid_bearer_token {{
            expression `{bearer_cel}`
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
            path /portal-resolver
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

    {token_auth_browser_matcher}

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
        caddyfile += '    ' + get_not_ready_handler_block() + '\n\n'

        if enable_auth and not is_port_auth_excluded(external_port):
            caddyfile += generate_auth_config(caddy_identifier, web_username, web_password, open_button_token, hostname, internal_port, flush_interval)
        else:
            caddyfile += generate_noauth_config(hostname, internal_port, flush_interval)
                                                               
        caddyfile += "}\n\n"

    return caddyfile, web_username, web_password, open_button_token

def get_not_ready_handler_block():
    """Placeholder shown while the backing service is still starting up.

    A CDN tunnel in front of the instance (Cloudflare) replaces an origin 5xx
    *body* with its own "Host is down" page, so a 502 loader never reaches a
    tunnelled user. We therefore serve the loader as HTTP 200 **only for requests
    that arrive over a Cloudflare tunnel** (they carry a ``Cf-Ray`` header); every
    other path — direct port access, Vast's own proxy, an uptime probe — keeps the
    real 502, so nothing that reads the status as a machine-readable "not ready"
    signal is misled (ADR 0017, option B).

    The ``X-Portal-Placeholder`` marker is emitted on both paths; 502.html's poll
    reloads only when that marker is absent *and* the status is not a 5xx (i.e. the
    backend is answering for real). Request matchers do not discriminate inside ``handle_errors`` on this
    Caddy build, so the CF decision is made at site scope via a request var and
    read back as the ``status`` placeholder. The matcher covers 502 (connection
    refused), 503 (no upstream) and 504 (bound but not answering yet, e.g. during
    model load); a backend's own 5xx *response* is passed through untouched, not
    swallowed here. The proxy blocks strip any upstream ``X-Portal-Placeholder`` so
    only Caddy can set it.
    """
    return '''@cf_tunnel header Cf-Ray *
    vars not_ready_status 502
    vars @cf_tunnel not_ready_status 200

    handle_errors 502 503 504 {
        rewrite * /502.html
        header X-Portal-Placeholder true
        header Cache-Control "no-store"
        file_server {
            status {vars.not_ready_status}
        }
    }'''

def get_cors_block():
    """
    Generate a CORS header block for Caddy.
    The behavior is controlled via environment variables:
      - CADDY_CORS_ALLOWED_ORIGINS: comma-separated list or single origin string.
        If unset or empty, no CORS headers are added.
      - CADDY_CORS_ALLOWED_METHODS: methods for Access-Control-Allow-Methods.
        Defaults to "GET, POST, PUT, DELETE, OPTIONS" if not set.
      - CADDY_CORS_ALLOWED_HEADERS: headers for Access-Control-Allow-Headers.
        Defaults to "Authorization, Content-Type" if not set.
    Does not override headers sent by backend services
    """
    allowed_origins = os.environ.get("CADDY_CORS_ALLOWED_ORIGINS", "").strip()
    if not allowed_origins:
        # Do not expose permissive CORS by default; require explicit configuration.
        return ""
    allowed_methods = os.environ.get(
        "CADDY_CORS_ALLOWED_METHODS",
        "GET, POST, PUT, DELETE, OPTIONS",
    ).strip()
    allowed_headers = os.environ.get(
        "CADDY_CORS_ALLOWED_HEADERS",
        "Authorization, Content-Type",
    ).strip()
    return f'''header ?Access-Control-Allow-Origin "{allowed_origins}"
            header ?Access-Control-Allow-Methods "{allowed_methods}"
            header ?Access-Control-Allow-Headers "{allowed_headers}"'''

def get_reverse_proxy_block(hostname, internal_port, flush_interval):
    use_localhost = False
    header_up_localhost = os.environ.get('CADDY_HEADER_UP_LOCALHOST', '')
    if header_up_localhost:
        ports_list = [p.strip() for p in header_up_localhost.split(',')]
        use_localhost = header_up_localhost.lower() == "true" or str(internal_port) in ports_list
    
    if use_localhost:
        host_header = f'''
            header_up Host localhost:{internal_port}
            header_up Origin {{forwarded_protocol}}://localhost:{internal_port}
        '''
    else:
        host_header = f"header_up Host {{upstream_hostport}}"
    
    return f'''reverse_proxy {hostname}:{internal_port} {{
            {host_header}
            header_up X-Forwarded-Proto {{forwarded_protocol}}
            header_up X-Real-IP {{real_ip}}
            header_down -X-Portal-Placeholder
            flush_interval {flush_interval}
        }}'''


def generate_noauth_config(hostname, internal_port, flush_interval):
    return f'''
    import real_ip_map
    import forwarded_protocol_map

    route /portal-resolver {{
        header Access-Control-Allow-Origin "*"
        header Access-Control-Allow-Methods "GET, OPTIONS"
        header Access-Control-Allow-Headers "*"
        respond 200
    }}

    handle {{
        {get_reverse_proxy_block(hostname, internal_port, flush_interval)}
    }}
    '''


def generate_auth_config(caddy_identifier, username, password, open_button_token, hostname, internal_port, flush_interval):
    hashed_password = subprocess.check_output([CADDY_BIN, 'hash-password', '-p', password]).decode().strip()
    cors_block = get_cors_block()
    proxy_block = get_reverse_proxy_block(hostname, internal_port, flush_interval)

    # Escape passwords for Caddy double-quoted string contexts (Set-Cookie headers)
    def caddy_quote_escape(s):
        return s.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '')

    safe_password = caddy_quote_escape(password)
    safe_open_button_token = caddy_quote_escape(open_button_token)

    return f'''
    import noauth_matcher
    import real_ip_map
    import forwarded_protocol_map

    route @noauth {{
        route /portal-resolver {{
            header Access-Control-Allow-Origin "*"
            header Access-Control-Allow-Methods "GET, OPTIONS"
            header Access-Control-Allow-Headers "*"
            respond 200
        }}

        handle {{
            {cors_block}
            {proxy_block}
        }}
    }}

    import token_auth_browser_matcher
    import token_auth_matcher
    import has_valid_auth_cookie_matcher
    import has_valid_bearer_token_matcher

    route @token_auth_browser {{
        header Set-Cookie "{caddy_identifier}_auth_token={safe_open_button_token}; Path=/; Max-Age=604800; HttpOnly; SameSite=lax"
        uri query -token
        redir * {{uri}} 302
    }}

    route @token_auth {{
        header Set-Cookie "{caddy_identifier}_auth_token={safe_open_button_token}; Path=/; Max-Age=604800; HttpOnly; SameSite=lax"
        uri query -token
        handle {{
            {cors_block}
            {proxy_block}
        }}
    }}

    route @has_valid_auth_cookie {{
        handle {{
            {cors_block}
            {proxy_block}
        }}
    }}

    route @has_valid_bearer_token {{
        handle {{
            {cors_block}
            {proxy_block}
        }}
    }}

    route {{
        basic_auth {{
            {username} "{hashed_password}"
        }}
        header Set-Cookie "{caddy_identifier}_auth_token={safe_password}; Path=/; Max-Age=604800; HttpOnly; SameSite=lax"

        handle {{
            {cors_block}
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