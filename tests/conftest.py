"""Make accidental real authentication or network access fail in offline tests."""

import socket

import pytest
from kubernetes import config


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline tests must mock authentication and network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(config, "load_kube_config", forbidden)
    monkeypatch.setattr(config, "load_incluster_config", forbidden)
