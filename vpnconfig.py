import re
from typing import Dict


OVPN_INLINE_TAGS = [
    'ca', 'cert', 'key', 'tls-auth', 'tls-crypt',
    'dh', 'pkcs12', 'secret', 'crl-verify',
]

OVPN_DIRECTIVE_FIELDS = {
    'remote', 'port', 'proto', 'dev', 'dev-type',
    'auth', 'cipher', 'comp-lzo', 'key-direction',
    'auth-user-pass', 'resolv-retry', 'nobind',
    'persist-key', 'persist-tun', 'remote-cert-tls',
    'verb', 'keepalive', 'tls-version-min', 'mtu',
    'tun-mtu', 'fragment', 'mssfix',
}

SIMPLE_DIRECTIVES = [
    'port', 'proto', 'dev', 'dev-type', 'auth',
    'cipher', 'key-direction', 'resolv-retry',
    'remote-cert-tls', 'verb', 'keepalive',
    'tls-version-min', 'mtu', 'tun-mtu', 'fragment',
    'mssfix',
]


def parse_ovpn_file(filepath: str) -> Dict[str, str]:
    with open(filepath, 'r') as f:
        content = f.read()
    return parse_ovpn_content(content)


def parse_ovpn_content(content: str) -> Dict[str, str]:
    config = {}
    inline_blocks = {}
    current_tag = None
    current_lines = []
    lines = content.splitlines()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or stripped.startswith(';'):
            continue

        if current_tag is not None:
            end_tag = f'</{current_tag}>'
            if stripped.lower() == end_tag:
                inline_blocks[current_tag] = '\n'.join(current_lines)
                current_tag = None
                current_lines = []
                continue
            current_lines.append(line)
            continue

        tag_match = re.match(r'^<(\S+)>$', stripped)
        if tag_match:
            tag_name = tag_match.group(1).lower()
            if tag_name in OVPN_INLINE_TAGS or True:
                current_tag = tag_name
                current_lines = []
                continue

        parts = stripped.split(None, 1)
        directive = parts[0].lower()
        value = parts[1] if len(parts) > 1 else ''

        if directive == 'remote':
            remote_parts = value.split()
            config['remote'] = remote_parts[0] if remote_parts else ''
            if len(remote_parts) >= 2:
                config['port'] = remote_parts[1]
            if len(remote_parts) >= 3:
                config['proto'] = remote_parts[2]

        elif directive == 'comp-lzo':
            config['comp-lzo'] = value if value else 'yes'

        elif directive == 'auth-user-pass':
            config['auth-user-pass'] = value if value else 'true'

        elif directive == 'nobind':
            config['nobind'] = 'true'

        elif directive == 'persist-key':
            config['persist-key'] = 'true'

        elif directive == 'persist-tun':
            config['persist-tun'] = 'true'

        elif directive in ('ca', 'cert', 'key', 'tls-auth', 'tls-crypt',
                           'dh', 'secret', 'crl-verify'):
            if value and not value.startswith('<'):
                config[directive] = value

        elif directive == 'lzo':
            config['comp-lzo'] = value if value else 'yes'

        elif directive in SIMPLE_DIRECTIVES:
            config[directive] = value

    for tag, block_content in inline_blocks.items():
        config[tag] = block_content

    return config


def get_ddwrt_nvram_from_config(vpn_config: Dict[str, str]) -> Dict[str, str]:
    nvram = {}

    if 'remote' in vpn_config:
        nvram['openvpncl_remoteip'] = vpn_config['remote']
    if 'port' in vpn_config:
        nvram['openvpncl_remoteport'] = vpn_config['port']

    proto = vpn_config.get('proto', 'udp')
    if proto == 'udp':
        nvram['openvpncl_proto'] = 'udp-client'
    elif proto == 'tcp':
        nvram['openvpncl_proto'] = 'tcp-client'
    else:
        nvram['openvpncl_proto'] = proto

    dev = vpn_config.get('dev', 'tun0')
    if dev.startswith('tun'):
        nvram['openvpncl_tuntap'] = 'tun'
    elif dev.startswith('tap'):
        nvram['openvpncl_tuntap'] = 'tap'
    else:
        nvram['openvpncl_tuntap'] = 'tun'

    if 'ca' in vpn_config:
        nvram['openvpncl_ca'] = vpn_config['ca']
    if 'cert' in vpn_config:
        nvram['openvpncl_client'] = vpn_config['cert']
    if 'key' in vpn_config:
        nvram['openvpncl_key'] = vpn_config['key']
    if 'cipher' in vpn_config:
        nvram['openvpncl_cipher'] = vpn_config['cipher']
    if 'auth' in vpn_config:
        nvram['openvpncl_sec'] = vpn_config['auth']

    comp_lzo = vpn_config.get('comp-lzo', 'no')
    if comp_lzo in ('yes', '1', 'true'):
        nvram['openvpncl_lzo'] = '1'
    else:
        nvram['openvpncl_lzo'] = '0'

    if 'auth-user-pass' in vpn_config:
        nvram['openvpncl_upauth'] = '1'
    else:
        nvram['openvpncl_upauth'] = '0'

    if 'username' in vpn_config:
        nvram['openvpncl_user'] = vpn_config['username']
    if 'password' in vpn_config:
        nvram['openvpncl_pass'] = vpn_config['password']

    if 'key-direction' in vpn_config:
        nvram['openvpncl_keydirection'] = vpn_config['key-direction']

    if 'tls-auth' in vpn_config:
        nvram['openvpncl_tlsauth'] = vpn_config['tls-auth']

    if 'tls-crypt' in vpn_config:
        nvram['openvpncl_tlsauth'] = vpn_config['tls-crypt']

    if 'mtu' in vpn_config or 'tun-mtu' in vpn_config:
        nvram['openvpncl_mtu'] = vpn_config.get('tun-mtu', vpn_config.get('mtu', '1400'))

    extra_lines = []
    extra_directives = [
        'resolv-retry', 'remote-cert-tls', 'keepalive',
        'mssfix', 'fragment', 'tls-version-min', 'verb',
    ]
    for directive in extra_directives:
        if directive in vpn_config:
            extra_lines.append(f'{directive} {vpn_config[directive]}')

    if vpn_config.get('nobind') == 'true':
        extra_lines.append('nobind')
    if vpn_config.get('persist-key') == 'true':
        extra_lines.append('persist-key')
    if vpn_config.get('persist-tun') == 'true':
        extra_lines.append('persist-tun')

    if extra_lines:
        nvram['openvpncl_config'] = '\n'.join(extra_lines)

    return nvram


def config_summary(vpn_config: Dict[str, str]) -> Dict[str, str]:
    summary = {}
    if 'remote' in vpn_config:
        summary['Server'] = vpn_config['remote']
    if 'port' in vpn_config:
        summary['Port'] = vpn_config['port']
    if 'proto' in vpn_config:
        summary['Protocol'] = vpn_config['proto']
    if 'dev' in vpn_config:
        summary['Device'] = vpn_config['dev']
    if 'cipher' in vpn_config:
        summary['Cipher'] = vpn_config['cipher']
    if 'auth' in vpn_config:
        summary['Auth'] = vpn_config['auth']
    comp = vpn_config.get('comp-lzo', 'no')
    if comp not in ('no', '', '0'):
        summary['Compression'] = comp
    if 'auth-user-pass' in vpn_config:
        summary['Auth Type'] = 'User/Password'
    return summary


def validate_vpn_config(vpn_config: Dict[str, str]) -> list:
    errors = []
    if not vpn_config.get('remote'):
        errors.append('Remote server is required')
    if not vpn_config.get('port'):
        errors.append('Port is required')
    if not vpn_config.get('ca'):
        errors.append('CA certificate is required')
    if not vpn_config.get('cert'):
        errors.append('Client certificate is required')
    if not vpn_config.get('key'):
        errors.append('Client key is required')
    return errors