from __future__ import annotations

from dataclasses import dataclass
import socket
import psutil


@dataclass(frozen=True)
class NetworkInterfaceOption:
    name: str
    ip: str
    is_up: bool
    is_link_local: bool


def list_ipv4_interfaces(include_down: bool = False) -> list[NetworkInterfaceOption]:
    """Enumerate local interfaces that have an IPv4 address.

    Returns a list suitable for a UI adapter-picker. Loopback is excluded.
    """
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()

    options: list[NetworkInterfaceOption] = []
    for if_name, addr_list in addrs.items():
        ipv4_addrs = [a.address for a in addr_list if a.family == socket.AF_INET and a.address]
        if not ipv4_addrs:
            continue

        # Exclude loopback
        ipv4_addrs = [ip for ip in ipv4_addrs if not ip.startswith("127.")]
        if not ipv4_addrs:
            continue

        # Prefer a non-link-local address if present.
        preferred = next((ip for ip in ipv4_addrs if not ip.startswith("169.254.")), ipv4_addrs[0])

        st = stats.get(if_name)
        is_up = bool(st.isup) if st is not None else True
        if not include_down and not is_up:
            continue

        options.append(
            NetworkInterfaceOption(
                name=if_name,
                ip=preferred,
                is_up=is_up,
                is_link_local=preferred.startswith("169.254."),
            )
        )

    options.sort(key=lambda o: (not o.is_up, o.is_link_local, o.name.lower(), o.ip))
    return options

