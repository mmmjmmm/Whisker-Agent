#!/usr/bin/env python
# -*- coding: utf-8 -*-

from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox


class FakeContainer:
    def __init__(self, attrs):
        self.attrs = attrs


def test_get_container_ip_falls_back_to_networks_when_top_level_ip_is_missing() -> None:
    container = FakeContainer({
        "NetworkSettings": {
            "Networks": {
                "manus-network": {
                    "IPAddress": "172.19.0.5",
                },
            },
        },
    })

    assert DockerSandbox._get_container_ip(container) == "172.19.0.5"


def test_get_container_ip_returns_none_when_network_settings_have_no_ip() -> None:
    container = FakeContainer({
        "NetworkSettings": {
            "Networks": {
                "manus-network": {},
            },
        },
    })

    assert DockerSandbox._get_container_ip(container) is None
