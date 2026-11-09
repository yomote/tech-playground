"""Owner allowlist policy for loopback services behind Tailscale Serve.

Serve must strip client-supplied identity headers and inject the login header.
Applications using this policy still define their own allowed routes.
"""

import ipaddress
import os
from urllib.parse import urlsplit


class TailnetViewPolicy:
    def __init__(self):
        origin = os.getenv('TP_TAILNET_ORIGIN', '')
        try:
            parsed = urlsplit(origin)
            valid = (parsed.scheme == 'https' and parsed.hostname and parsed.hostname.endswith('.ts.net')
                     and parsed.port is not None and 1 <= parsed.port <= 65535
                     and not parsed.username and not parsed.password
                     and not parsed.path and not parsed.query and not parsed.fragment
                     and parsed.netloc == parsed.hostname + ':' + str(parsed.port))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('Exact Tailnet HTTPS origin with an explicit port required')
        logins = os.getenv('TP_TAILNET_ALLOWED_LOGINS', '').split(',')
        if (not logins or any(not value.strip() or len(value.strip()) > 254
                              or any(ord(char) < 33 or ord(char) > 126 for char in value.strip())
                              for value in logins)):
            raise ValueError('A nonempty explicit Tailnet login allowlist is required')
        self.origin = origin
        self.host = parsed.netloc
        self.logins = frozenset(value.strip() for value in logins)

    def allows(self, headers, client_address):
        try:
            if not ipaddress.ip_address(client_address[0]).is_loopback:
                return False
        except (ValueError, IndexError):
            return False
        hosts = headers.get_all('Host', [])
        origins = headers.get_all('Origin', [])
        logins = headers.get_all('Tailscale-User-Login', [])
        return (len(hosts) == 1 and hosts[0] == self.host
                and len(origins) <= 1 and (not origins or origins[0] == self.origin)
                and len(logins) == 1 and logins[0] in self.logins)
