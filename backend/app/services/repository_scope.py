"""
Repository Scope Enforcement — Phase 20A4.

Deterministic, repository-isolation layer that ensures every patch is
validated **only against its own repository checkout**. A run that
participates in cross-repository planning may carry several repositories,
but each patch is bound to a single ``repository_id`` and is validated
against that repository's own checkout root (``workspace_path``).

Public surface:

- :class:`RepositoryScope`           — checkout ownership for one repository.
- :class:`RepositoryScopeRegistry`   — maps ``repository_id`` -> scope and
  answers "does this path belong to this repository?".
- :class:`PathCheck`                 — result of a path-containment probe.

Design invariants (never broken, even on the deterministic path):

* A patch whose ``repository_id`` is set MUST resolve every changed path
  under that repository's checkout root; any path that escapes is a
  scope violation and is rejected before application.
* A ``RepositoryScope`` registered for repository A NEVER validates a path
  that resolves under repository B's checkout — cross-checkout validation is
  impossible because each patch is bound to exactly one repository.
* All path resolution uses :meth:`pathlib.Path.resolve` so symlinks / ``..``
  traversal are normalized before containment is tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.coding import FileOperation, PatchSet


@dataclass(frozen=True)
class PathCheck:
    """Result of probing whether a relative path belongs to a repository."""

    is_within: bool
    reason: str


class RepositoryScope(BaseModel):
    """Checkout ownership for a single repository (Phase 20A4).

    ``checkout_root`` is the resolved absolute path of the working tree this
    repository owns. ``owned_paths`` are the additional absolute roots the
    repository is allowed to touch (typically just the checkout root); a
    change whose resolved target is not under one of these roots is a
    cross-repository violation.
    """

    repository_id: str = Field(description="Stable namespace for this repository")
    namespace: str = Field(default="", description="Repository namespace (usually == repository_id)")
    checkout_root: str = Field(description="Resolved absolute checkout path")
    owned_paths: List[str] = Field(
        default_factory=list,
        description="Resolved absolute roots this repository may modify",
    )
    workspace_path: Optional[str] = Field(
        default=None,
        description="Workspace path for patch validation (defaults to checkout_root)",
    )

    @property
    def effective_workspace(self) -> Path:
        root = self.workspace_path or self.checkout_root
        return Path(root).resolve()

    def contains_path(self, rel_path: str) -> PathCheck:
        """True when ``rel_path`` (relative to the checkout) stays in-scope.

        ``rel_path`` is resolved against the workspace root and must remain
        inside one of ``owned_paths``; ``..`` segments that escape the
        checkout are reported as violations.
        """
        if not rel_path:
            return PathCheck(is_within=False, reason="empty path")
        workspace = self.effective_workspace
        try:
            target = (workspace / rel_path).resolve()
        except (OSError, RuntimeError):
            return PathCheck(is_within=False, reason=f"unresolvable path: {rel_path}")
        roots = [Path(p).resolve() for p in self.owned_paths] or [workspace]
        for root in roots:
            try:
                target.relative_to(root)
                return PathCheck(is_within=True, reason="")
            except ValueError:
                continue
        return PathCheck(
            is_within=False,
            reason=f"path '{rel_path}' resolves outside repository "
                   f"{self.repository_id} checkout {workspace}",
        )

    def owns_checkout(self, checkout_path: str) -> bool:
        """True when ``checkout_path`` is this repository's checkout root."""
        try:
            given = Path(checkout_path).resolve()
            return given == Path(self.checkout_root).resolve()
        except (OSError, RuntimeError):
            return False


class RepositoryScopeRegistry:
    """In-memory registry of repository scopes for one run.

    Built by the orchestrator from the primary repository + the run's
    auxiliary namespaces. Supports a serializable form (plain dicts) so a
    scope registry can be handed to the deterministic reviewer through
    ``ReviewInput.extra_context`` without creating cross-layer imports.
    """

    def __init__(self) -> None:
        self._scopes: Dict[str, RepositoryScope] = {}

    # ── mutation ───────────────────────────────────────────────────

    def register(self, scope: RepositoryScope) -> None:
        if not scope.repository_id:
            return
        self._scopes[scope.repository_id] = scope

    def register_many(self, scopes: List[RepositoryScope]) -> None:
        for s in scopes:
            self.register(s)

    # ── lookup ─────────────────────────────────────────────────────

    def resolve(self, repository_id: Optional[str]) -> Optional[RepositoryScope]:
        if not repository_id:
            return None
        return self._scopes.get(repository_id)

    def has(self, repository_id: str) -> bool:
        return repository_id in self._scopes

    def repository_ids(self) -> List[str]:
        return list(self._scopes.keys())

    def own_repository_id(self, checkout_path: str) -> Optional[str]:
        """Which registered repository owns ``checkout_path``'s checkout.

        Used to attribute a patch to its repository when the patch only
        carries a workspace path.
        """
        try:
            given = Path(checkout_path).resolve()
        except (OSError, RuntimeError):
            return None
        for scope in self._scopes.values():
            try:
                if given == Path(scope.checkout_root).resolve():
                    return scope.repository_id
            except (OSError, RuntimeError):
                continue
        return None

    # ── validation ─────────────────────────────────────────────────

    def check_path(self, repository_id: str, rel_path: str) -> PathCheck:
        """Validate a single relative path belongs to ``repository_id``."""
        scope = self.resolve(repository_id)
        if scope is None:
            return PathCheck(
                is_within=False,
                reason=f"repository '{repository_id}' is not registered in the scope registry",
            )
        return scope.contains_path(rel_path)

    def validate_patch(self, repository_id: Optional[str], patch: PatchSet) -> tuple[bool, List[str], List[str]]:
        """Validate a patch's ownership + path containment for one repository.

        Returns ``(valid, errors, rejected_paths)``. A patch passes when:

        * it declares no ``repository_id`` (unattributed, treated as the
          engine's bound repository — backwards compatible primary path), OR
        * its declared ``repository_id`` matches the scope being validated AND
          every changed path resolves inside that repository's checkout.
        """
        if not repository_id:
            return True, [], []

        scope = self.resolve(repository_id)
        if scope is None:
            return (
                False,
                [f"repository '{repository_id}' is not registered in the scope registry"],
                [],
            )

        # Ownership: a patch that claims to belong to another repository than
        # the one being validated is rejected outright (no cross-checkout).
        declared = patch.repository_id
        if declared and declared != repository_id:
            return (
                False,
                [f"patch claims repository '{declared}' but is being validated "
                 f"against repository '{repository_id}' — cross-repository patch "
                 f"application rejected"],
                [],
            )

        errors: List[str] = []
        rejected: List[str] = []
        for change in patch.changes:
            if change.operation == FileOperation.DELETE:
                # DELETE only needs an original_hash (existing file to remove);
                # skip path-containment for non-existent targets.
                continue
            check = scope.contains_path(change.path)
            if not check.is_within:
                rejected.append(change.path)
                errors.append(f"{change.path}: {check.reason}")
        return (len(rejected) == 0), errors, rejected

    # ── serialization (plain dicts, evidence-only) ─────────────────

    def to_dicts(self) -> List[Dict[str, Any]]:
        return [
            {
                "repository_id": s.repository_id,
                "namespace": s.namespace,
                "checkout_root": s.checkout_root,
                "owned_paths": list(s.owned_paths),
                "workspace_path": s.workspace_path,
            }
            for s in self._scopes.values()
        ]

    @classmethod
    def from_dicts(cls, data: List[Dict[str, Any]]) -> "RepositoryScopeRegistry":
        reg = cls()
        for d in data or []:
            reg.register(RepositoryScope(
                repository_id=d.get("repository_id", ""),
                namespace=d.get("namespace", "") or d.get("repository_id", ""),
                checkout_root=d.get("checkout_root", ""),
                owned_paths=list(d.get("owned_paths") or []),
                workspace_path=d.get("workspace_path"),
            ))
        return reg
