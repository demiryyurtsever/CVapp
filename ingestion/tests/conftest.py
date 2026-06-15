"""Test guards.

CLAUDE.md rule 5 / dossier §3.12: tests never touch live endpoints. This blocks
outbound connections at the socket layer for every test, so an accidental
``fetch()`` (or any network call) fails loudly instead of hitting a real board.
"""

from __future__ import annotations

import socket

import pytest


class _NoNetworkSocket(socket.socket):
    def connect(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("Network access is disabled in tests (CLAUDE.md rule 5).")

    def connect_ex(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("Network access is disabled in tests (CLAUDE.md rule 5).")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "socket", _NoNetworkSocket)
