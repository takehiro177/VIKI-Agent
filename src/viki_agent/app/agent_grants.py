"""Per-process keys for the `viki-agent` command an ordinary chat's tools run.

Once `SessionManager.bind_agent_cli` has installed the registry, each spawn of an ordinary chat
made with `agent_cli`, resume included, gets a new grant. Its variables join that Claude
process's environment, and the driver revokes it when the process closes or exits. Grants live
in memory only, and nothing here logs an id or a key.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ENV_URL = "VIKI_AGENT_URL"
ENV_KEY = "VIKI_AGENT_KEY"
ENV_CLI = "VIKI_AGENT_CLI"
# Above the highest live-session limit Settings allows: reaching it means keys outlive their
# processes, and a new chat then starts without one rather than not at all.
MAX_GRANTS = 1024

log = logging.getLogger(__name__)


@dataclass(repr=False)  # the generated repr would print the key
class AgentGrant:
    registry: AgentGrants
    session_id: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))

    def environment(self) -> dict[str, str]:
        return {
            ENV_URL: f"http://127.0.0.1:8765/api/agent/{self.id}",
            ENV_KEY: self.token,
            ENV_CLI: str(self.registry.cli),
        }

    def revoke(self) -> None:
        self.registry.revoke(self.id)


class AgentGrants:
    """The live grants by id. `cli` is the absolute path of the `viki-agent` command."""

    def __init__(self, cli: Path) -> None:
        self.cli = cli
        self._grants: dict[str, AgentGrant] = {}

    def issue(self, session_id: str) -> AgentGrant | None:
        """A new grant for one Claude process of `session_id`; None once MAX_GRANTS are live."""
        if len(self._grants) >= MAX_GRANTS:
            log.warning("agent CLI key limit reached: a chat starts without one")
            return None
        grant = AgentGrant(self, session_id)
        self._grants[grant.id] = grant
        return grant

    def revoke(self, grant_id: str) -> None:
        self._grants.pop(grant_id, None)

    def find(self, grant_id: str, credential: str) -> AgentGrant | None:
        """The live grant whose key `credential` is, compared in constant time; else None.

        A key is ASCII, so any other credential is refused before the compare, which would
        raise on it."""
        grant = self._grants.get(grant_id)
        if (
            grant is None
            or not credential.isascii()
            or not secrets.compare_digest(credential, grant.token)
        ):
            return None
        return grant

    def live(self, grant_id: str) -> bool:
        return grant_id in self._grants
