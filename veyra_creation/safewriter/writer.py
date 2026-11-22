"""Minimal production-shaped SafeWriter for Phase 3.2.2.1-2.4.

The implementation deliberately supports only ``create`` and ``replace``.
``delete`` and ``recover`` are explicit ``E_NOT_IMPLEMENTED`` refusals until the
backup/recovery phases.  It is not wired into ProjectStore or the CLI.
"""

from __future__ import annotations

import hashlib
import ntpath
import os
import secrets
from dataclasses import dataclass
from typing import Any, Callable

from ..canonical import Clock, now_iso
from . import native
from .anchors import DirectoryAnchor, RootAnchor, _close_quiet, _fail_status
from .errors import (
    E_ACCESS_DENIED,
    E_ANCESTOR_CHANGED,
    E_COMMIT_UNCERTAIN,
    E_IO,
    E_JOURNAL_WRITE_FAILED,
    E_LOCK_CONFLICT,
    E_NOT_IMPLEMENTED,
    E_OPERATION_INVALID,
    E_PATH_INVALID,
    E_REPARSE_REFUSED,
    E_ROOT_CHANGED,
    E_SHARING_VIOLATION,
    E_TARGET_ABSENT,
    E_TARGET_CHANGED,
    E_TARGET_EXISTS,
    SafeWriterError,
)
from .identities import ExpectedTarget, Identity, RequestRef, RootIdentity, TargetFacts
from .journal import OperationJournal
from .lock import LockManager, OwnerInfo
from .policy import (
    validate_batch_id,
    validate_content,
    validate_op_id,
    validate_reason,
    validate_ref,
    validate_relative_path,
    validate_sha256,
    temp_name,
    quarantine_name,
)
from .results import (
    COMPLETED,
    FAILED,
    RECOVERY_REQUIRED,
    REFUSED,
    UNCERTAIN,
    WriteResult,
)
from .target import TargetAnchor

TOOL_VERSION = "3.2.2.1-2.4-prototype"
_TEMP_ACCESS = (
    native.FILE_READ_DATA
    | native.FILE_WRITE_DATA
    | native.FILE_READ_ATTRIBUTES
    | native.DELETE
    | native.SYNCHRONIZE
)
_PREFLIGHT_CODES = frozenset(
    {
        E_ACCESS_DENIED,
        E_LOCK_CONFLICT,
        E_OPERATION_INVALID,
        E_PATH_INVALID,
        E_REPARSE_REFUSED,
        E_ROOT_CHANGED,
        E_SHARING_VIOLATION,
        E_TARGET_ABSENT,
        E_TARGET_CHANGED,
        E_TARGET_EXISTS,
    }
)


@dataclass(frozen=True)
class CreateRequest:
    ref: RequestRef
    relative_path: str
    content: bytes


@dataclass(frozen=True)
class ReplaceRequest:
    ref: RequestRef
    relative_path: str
    content: bytes
    expected: ExpectedTarget


@dataclass
class _Session:
    root: RootAnchor
    creation: DirectoryAnchor
    lock: Any
    operations: DirectoryAnchor
    journal: OperationJournal


class SafeWriter:
    """Strict create/replace layer over a handle-anchored project root."""

    def __init__(
        self,
        project_root: str,
        *,
        expected_root_identity: RootIdentity | None = None,
        clock: Clock | None = None,
        fault_hook: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.project_root = project_root
        self.expected_root_identity = expected_root_identity
        self.clock = clock
        self.fault_hook = fault_hook

    # ------------------------------------------------------------------ public
    def create(self, request: CreateRequest) -> WriteResult:
        ref = getattr(request, "ref", None)
        try:
            self._validate_ref(ref)
            self._validate_create(request)
        except (SafeWriterError, TypeError, ValueError) as exc:
            return self._plain_refusal(ref, "create", exc)
        session = None
        temp: TargetAnchor | None = None
        opened: TargetAnchor | None = None
        temp_rename_attempted = False
        parent: DirectoryAnchor | None = None
        try:
            try:
                session = self._open_session(ref)
            except SafeWriterError as exc:
                return self._plain_session_failure(ref, "create", exc)

            try:
                parent = self._ensure_create_parent(session, ref, request.relative_path)
                target_name = validate_relative_path(request.relative_path)[-1]
                parent_parts = validate_relative_path(request.relative_path)[:-1]
                TargetAnchor.probe_absent(parent, target_name)
                self._append(session, ref, "TARGET_ABSENT", "PROBE_TARGET_ABSENT", relative_path=request.relative_path)
                self._append(session, ref, "CREATE_INTENT", "CREATE_FILE", relative_path=request.relative_path)
            except SafeWriterError as exc:
                return self._finish_failure(session, ref, "create", exc, status=REFUSED)

            try:
                temp, expected_sha = self._create_temp(parent, ref.op_id, request.content)
                self._append(
                    session,
                    ref,
                    "TEMP_READY",
                    "TEMP_WRITTEN",
                    temp_name=temp.name,
                    temp_identity=temp.facts.identity.as_dict(),
                    temp_sha256=temp.facts.sha256,
                    temp_size=temp.facts.size,
                )
                self._maybe_fault("create:temp_ready", temp=temp.name)
                TargetAnchor.probe_absent(parent, target_name)
                self._verify_binding(session, parent_parts, parent)
                self._append(session, ref, "COMMIT_INTENT", "RENAME_TEMP_TO_TARGET", target_name=target_name)
            except SafeWriterError as exc:
                self._cleanup_temp(temp)
                temp = None
                return self._finish_failure(session, ref, "create", exc, status=FAILED)

            # From here the temp handle may have been renamed.  Never delete it
            # by a stale assumption: an uncertain rename must preserve evidence.
            temp_rename_attempted = True
            try:
                self._rename(temp.handle, parent, target_name, context=f"commit target {target_name!r}")
            except SafeWriterError as exc:
                if exc.code == E_COMMIT_UNCERTAIN:
                    return self._uncertain(
                        session,
                        ref,
                        "create",
                        exc,
                        temp=temp,
                        target_name=target_name,
                    )
                return self._finish_failure(session, ref, "create", exc, status=FAILED)

            opened: TargetAnchor | None = None
            try:
                facts = temp.read_facts()
                if facts.identity != temp.facts.identity or facts.sha256 != expected_sha:
                    raise SafeWriterError(E_COMMIT_UNCERTAIN, "target handle facts changed after commit")
                # Close our commit handle before opening the name again.  Its
                # write/DELETE access would otherwise make a READ-only verifier
                # fail with a sharing violation.
                temp.close()
                opened = TargetAnchor.open_existing(
                    parent,
                    target_name,
                    expected=ExpectedTarget(facts.identity, expected_sha),
                )
                if opened.facts.identity != facts.identity:
                    raise SafeWriterError(E_COMMIT_UNCERTAIN, "target name does not resolve to committed handle")
                self._verify_binding(session, parent_parts, parent)
                self._append(
                    session,
                    ref,
                    "COMMITTED",
                    "CREATE_COMMITTED",
                    target_name=target_name,
                    identity=facts.identity.as_dict(),
                    sha256=expected_sha,
                    size=facts.size,
                )
            except Exception as exc:
                temp.close()
                return self._uncertain(session, ref, "create", exc, temp=temp, target_name=target_name)
            finally:
                # Keep the target name held until COMMITTED is durable, then
                # release it.  Without this handle a third party could rename
                # the new target between verification and the journal record.
                if opened is not None:
                    opened.close()

            self._maybe_fault("create:committed", target_name=target_name)
            return WriteResult(
                status=COMPLETED,
                operation_id=ref.op_id,
                action="create",
                identity=facts.identity.as_dict(),
                sha256=expected_sha,
                size=facts.size,
                temp_name=temp.name,
                journal_path=self._journal_path(session, ref),
                durable=True,
                details={"target_name": target_name, "root_identity": session.root.identity.as_dict()},
            )
        finally:
            if temp is not None:
                if temp_rename_attempted:
                    # The handle may name the committed target or a retained
                    # temp; close it without deleting either.
                    temp.close()
                else:
                    self._cleanup_temp(temp)
            if parent is not None:
                parent.close()
            if session is not None:
                self._close_session(session)

    def replace(self, request: ReplaceRequest) -> WriteResult:
        ref = getattr(request, "ref", None)
        try:
            self._validate_ref(ref)
            self._validate_replace(request)
        except (SafeWriterError, TypeError, ValueError) as exc:
            return self._plain_refusal(ref, "replace", exc)

        session = None
        temp: TargetAnchor | None = None
        temp_rename_attempted = False
        target: TargetAnchor | None = None
        quarantine_dir: DirectoryAnchor | None = None
        parent: DirectoryAnchor | None = None
        quarantined = False
        try:
            try:
                session = self._open_session(ref)
            except SafeWriterError as exc:
                return self._plain_session_failure(ref, "replace", exc)

            try:
                components = validate_relative_path(request.relative_path)
                target_name = components[-1]
                parent_parts = components[:-1]
                if parent_parts:
                    parent = session.root.open_path(parent_parts, create=False)
                    self._close_intermediate_dirs(parent)
                else:
                    parent = session.root.final_anchor()
                target = TargetAnchor.open_existing(
                    parent, target_name, expected=request.expected, for_replace=True
                )
                self._append(
                    session,
                    ref,
                    "TARGET_VERIFIED",
                    "VERIFY_TARGET",
                    target_name=target_name,
                    identity=target.facts.identity.as_dict(),
                    sha256=target.facts.sha256,
                )
            except SafeWriterError as exc:
                return self._finish_failure(session, ref, "replace", exc, status=REFUSED)

            try:
                quarantine_dir, quarantine_label = self._ensure_quarantine_dir(session, ref)
                self._append(
                    session,
                    ref,
                    "QUARANTINE_DIR_READY",
                    "QUARANTINE_DIR_CREATED",
                    quarantine=quarantine_label,
                )
                TargetAnchor.probe_absent(quarantine_dir, quarantine_label)
                self._append(
                    session,
                    ref,
                    "QUARANTINE_DIR_READY",
                    "QUARANTINE_NAME_FREE",
                    quarantine=quarantine_label,
                )
            except SafeWriterError as exc:
                return self._finish_failure(session, ref, "replace", exc, status=FAILED)

            try:
                temp, expected_sha = self._create_temp(parent, ref.op_id, request.content)
                self._append(
                    session,
                    ref,
                    "TEMP_READY",
                    "TEMP_WRITTEN",
                    temp_name=temp.name,
                    temp_identity=temp.facts.identity.as_dict(),
                    temp_sha256=temp.facts.sha256,
                    temp_size=temp.facts.size,
                )
                self._maybe_fault("replace:temp_ready", temp=temp.name)
                target.verify_unchanged(request.expected)
                self._verify_binding(session, parent_parts, parent)
                self._append(
                    session,
                    ref,
                    "QUARANTINE_INTENT",
                    "MOVE_TARGET_TO_QUARANTINE",
                    target_name=target_name,
                    quarantine=quarantine_label,
                )
            except SafeWriterError as exc:
                self._cleanup_temp(temp)
                temp = None
                return self._finish_failure(session, ref, "replace", exc, status=FAILED)

            # The target rename is the first action that removes the user's
            # target name.  Never delete a temp or quarantine object afterwards.
            try:
                self._rename(target.handle, quarantine_dir, quarantine_label, context="quarantine target")
                quarantined = True
                target.name = quarantine_label
            except SafeWriterError as exc:
                return self._finish_failure(
                    session, ref, "replace", exc, status=RECOVERY_REQUIRED
                )

            try:
                qfacts = target.read_facts()
                if qfacts.identity != target.facts.identity or qfacts.sha256 != request.expected.sha256:
                    raise SafeWriterError(E_COMMIT_UNCERTAIN, "quarantine handle no longer matches original target")
                self._append(
                    session,
                    ref,
                    "QUARANTINED",
                    "TARGET_QUARANTINED",
                    quarantine=quarantine_label,
                    identity=qfacts.identity.as_dict(),
                    sha256=qfacts.sha256,
                    size=qfacts.size,
                )
                self._append(session, ref, "COMMIT_INTENT", "RENAME_TEMP_TO_TARGET", target_name=target_name)
                # Keep the quarantine handle open through the commit.  The
                # handle now names the quarantine object, not the new target,
                # so it does not conflict with the final rename/verification;
                # keeping it open prevents a third party from renaming or
                # deleting the quarantine while the replace is in progress.
            except SafeWriterError as exc:
                return self._finish_failure(
                    session, ref, "replace", exc, status=RECOVERY_REQUIRED
                )

            temp_rename_attempted = True
            try:
                self._rename(temp.handle, parent, target_name, context=f"commit target {target_name!r}")
            except SafeWriterError as exc:
                return self._finish_failure(
                    session, ref, "replace", exc, status=RECOVERY_REQUIRED
                )

            try:
                facts = temp.read_facts()
                if facts.identity != temp.facts.identity or facts.sha256 != expected_sha:
                    raise SafeWriterError(E_COMMIT_UNCERTAIN, "committed temp handle facts changed")
                temp.close()
                opened = TargetAnchor.open_existing(
                    parent,
                    target_name,
                    expected=ExpectedTarget(facts.identity, expected_sha),
                )
                try:
                    if opened.facts.identity != facts.identity:
                        raise SafeWriterError(E_COMMIT_UNCERTAIN, "target name does not resolve to committed handle")
                finally:
                    opened.close()
            except Exception as exc:
                temp.close()
                return self._uncertain(
                    session,
                    ref,
                    "replace",
                    exc,
                    temp=temp,
                    target_name=target_name,
                    quarantine=quarantine_label,
                )

            try:
                self._verify_binding(session, parent_parts, parent)
            except SafeWriterError as exc:
                return self._uncertain(
                    session,
                    ref,
                    "replace",
                    exc,
                    temp=temp,
                    target_name=target_name,
                    quarantine=quarantine_label,
                )

            try:
                self._append(
                    session,
                    ref,
                    "COMMITTED",
                    "REPLACE_COMMITTED",
                    target_name=target_name,
                    identity=facts.identity.as_dict(),
                    sha256=expected_sha,
                    size=facts.size,
                    quarantine=quarantine_label,
                )
            except SafeWriterError as exc:
                return self._uncertain(
                    session,
                    ref,
                    "replace",
                    exc,
                    temp=temp,
                    target_name=target_name,
                    quarantine=quarantine_label,
                )

            self._maybe_fault("replace:committed", target_name=target_name)
            return WriteResult(
                status=COMPLETED,
                operation_id=ref.op_id,
                action="replace",
                identity=facts.identity.as_dict(),
                sha256=expected_sha,
                size=facts.size,
                temp_name=temp.name,
                quarantine_name=quarantine_label,
                journal_path=self._journal_path(session, ref),
                durable=True,
                details={
                    "target_name": target_name,
                    "quarantine": quarantine_label,
                    "root_identity": session.root.identity.as_dict(),
                },
            )
        finally:
            if temp is not None:
                if not temp_rename_attempted:
                    self._cleanup_temp(temp)
                else:
                    # A failed rename may have committed the name.  Closing the
                    # handle is not deleting the retained file.
                    temp.close()
            if target is not None:
                target.close()
            if quarantine_dir is not None:
                quarantine_dir.close()
            if parent is not None:
                parent.close()
            if session is not None:
                self._close_session(session)

    def delete(self, request: Any) -> WriteResult:
        ref = getattr(request, "ref", None)
        return WriteResult(
            status=REFUSED,
            operation_id=getattr(ref, "op_id", "unknown"),
            action="delete",
            code=E_NOT_IMPLEMENTED,
            message="delete is outside the 2.4 prototype scope",
            durable=False,
        )

    def recover(self, request: Any) -> WriteResult:
        ref = getattr(request, "ref", None)
        return WriteResult(
            status=REFUSED,
            operation_id=getattr(ref, "op_id", "unknown"),
            action="recover",
            code=E_NOT_IMPLEMENTED,
            message="recovery is outside the 2.4 prototype scope",
            durable=False,
        )

    # ------------------------------------------------------------------ internals
    def _validate_ref(self, ref: RequestRef | None) -> None:
        if not isinstance(ref, RequestRef):
            raise SafeWriterError(E_OPERATION_INVALID, "request.ref must be a RequestRef")
        validate_batch_id(ref.batch_id)
        validate_op_id(ref.op_id)
        validate_ref(ref.approval_ref, field="approval_ref")
        validate_ref(ref.plan_ref, field="plan_ref")
        validate_sha256(ref.plan_sha256)
        validate_reason(ref.reason)

    def _validate_create(self, request: CreateRequest) -> None:
        if not isinstance(request, CreateRequest):
            raise SafeWriterError(E_OPERATION_INVALID, "create requires a CreateRequest")
        validate_relative_path(request.relative_path)
        validate_content(request.content)

    def _validate_replace(self, request: ReplaceRequest) -> None:
        if not isinstance(request, ReplaceRequest):
            raise SafeWriterError(E_OPERATION_INVALID, "replace requires a ReplaceRequest")
        if not isinstance(request.expected, ExpectedTarget):
            raise SafeWriterError(E_OPERATION_INVALID, "replace requires ExpectedTarget")
        validate_relative_path(request.relative_path)
        validate_content(request.content)

    def _open_session(self, ref: RequestRef) -> _Session:
        root = RootAnchor.open(self.project_root, expected=self.expected_root_identity)
        creation = None
        lock = None
        operations = None
        journal = None
        try:
            # Keep verification inside the cleanup boundary: a root that fails
            # verification must not leave its freshly opened handle chain open.
            root.verify()
            creation = root.open_child_dir("creation", create=True)
            owner = OwnerInfo(
                owner_token=secrets.token_hex(16),
                pid=os.getpid(),
                batch_id=ref.batch_id,
                root_identity=root.identity.as_dict(),
                host=os.environ.get("COMPUTERNAME", ""),
                tool_version=TOOL_VERSION,
                acquired_at=now_iso(self.clock),
            )
            lock = LockManager.acquire(creation, owner=owner)
            operations = creation.open_child("operations", create=True)
            journal = OperationJournal.open(operations, ref.batch_id, clock=self.clock)
            return _Session(root=root, creation=creation, lock=lock, operations=operations, journal=journal)
        except Exception:
            if journal is not None:
                journal.close()
            if lock is not None:
                lock.release(suppress_errors=True)
            if operations is not None:
                operations.close()
            if creation is not None:
                creation.close()
            root.close()
            raise

    def _close_session(self, session: _Session) -> None:
        try:
            session.journal.close()
        except Exception:
            pass
        try:
            session.lock.release(suppress_errors=True)
        except Exception:
            pass
        for anchor in (session.operations, session.creation):
            try:
                anchor.close()
            except Exception:
                pass
        try:
            session.root.close()
        except Exception:
            pass

    def _ensure_create_parent(
        self, session: _Session, ref: RequestRef, relative_path: str
    ) -> DirectoryAnchor:
        components = validate_relative_path(relative_path)
        parent_parts = components[:-1]
        if not parent_parts:
            return session.root.final_anchor()
        self._append(
            session,
            ref,
            "PARENT_DIRS_INTENT",
            "ENSURE_PARENT_DIRS",
            relative_path=relative_path,
            parent_components=list(parent_parts),
        )
        parent = session.root.open_path(parent_parts, create=True)
        try:
            self._close_intermediate_dirs(parent)
            self._append(
                session,
                ref,
                "PARENT_READY",
                "PARENT_DIRS_READY",
                parent_identity=parent.identity.as_dict(),
            )
            return parent
        except Exception:
            # The caller receives the parent only on success.  If the journal
            # append fails after open_path created the final directory, this is
            # the last owner of that handle.
            parent.close()
            raise

    def _verify_binding(
        self, session: _Session, parent_parts: tuple[str, ...], parent: DirectoryAnchor
    ) -> None:
        """Re-resolve the root and parent path by name and compare identities.

        Handles alone keep an object alive, but if the path was renamed or
        substituted the user-visible name no longer designates that object.
        This check is deliberately fail-closed; it does not replace the handle
        chain or authorize following a reparse point.
        """
        try:
            fresh = RootAnchor.open(session.root.root_path)
        except SafeWriterError as exc:
            raise SafeWriterError(E_ROOT_CHANGED, f"project root path no longer resolves: {exc}") from exc
        try:
            if fresh.identity != session.root.identity:
                raise SafeWriterError(E_ROOT_CHANGED, "project root path now resolves to a different object")
            if not parent_parts:
                return
            try:
                current = fresh.open_path(parent_parts, create=False)
            except SafeWriterError as exc:
                raise SafeWriterError(E_ANCESTOR_CHANGED, f"parent path no longer resolves: {exc}") from exc
            try:
                if current.identity != parent.identity:
                    raise SafeWriterError(E_ANCESTOR_CHANGED, "parent path now resolves to a different directory")
            finally:
                self._close_intermediate_dirs(current)
                current.close()
        finally:
            fresh.close()

    @staticmethod
    def _close_intermediate_dirs(anchor: DirectoryAnchor) -> None:
        """Close handles above ``anchor`` that are no longer needed.

        The deepest handle is kept; only the anchors used to reach it are
        closed.  ``RootAnchor``'s final anchor does not own its handle, so the
        root chain is preserved.
        """
        node = anchor.parent
        while node is not None:
            if node.owns_handle:
                node.close()
            node = node.parent

    def _ensure_quarantine_dir(
        self, session: _Session, ref: RequestRef
    ) -> tuple[DirectoryAnchor, str]:
        self._append(session, ref, "QUARANTINE_DIR_INTENT", "ENSURE_QUARANTINE_DIR")
        try:
            qroot = session.creation.open_child("quarantine", create=True)
            try:
                qbatch = qroot.open_child(ref.batch_id, create=True)
                try:
                    qop = qbatch.open_child(ref.op_id, create=True)
                except Exception:
                    qbatch.close()
                    raise
            except Exception:
                qroot.close()
                raise
        except SafeWriterError:
            raise
        # The operation anchor's handle is all the rename needs; the two
        # ancestor handles can be released now instead of leaking until GC.
        qbatch.close()
        qroot.close()
        label = quarantine_name(ref.op_id)
        return qop, label

    def _create_temp(self, parent: DirectoryAnchor, op_id: str, content: bytes) -> tuple[TargetAnchor, str]:
        handle = None
        status = 0
        for _ in range(4):
            name = temp_name(op_id, secrets.token_hex(8))
            handle, status = native.nt_open_relative(
                parent.handle,
                name,
                desired_access=_TEMP_ACCESS,
                share_access=native.FILE_SHARE_READ,
                disposition=native.FILE_CREATE,
                options=native.FILE_NON_DIRECTORY_FILE,
                attributes=native.FILE_ATTRIBUTE_NORMAL | native.FILE_ATTRIBUTE_TEMPORARY,
            )
            if status == native.STATUS_OBJECT_NAME_COLLISION:
                handle = None
                continue
            if status < 0 or handle is None:
                _fail_status(status, f"create temp for {op_id!r}", default=E_IO)
            break
        if handle is None:
            raise SafeWriterError(E_TARGET_EXISTS, "could not allocate a unique temp name")
        temp = TargetAnchor.adopt_created(parent, name, handle)
        try:
            native.write_all(temp.handle, content, truncate=True)
            native.flush(temp.handle)
            expected_sha = hashlib.sha256(content).hexdigest()
            facts = temp.read_facts()
            if facts.size != len(content) or facts.sha256 != expected_sha:
                raise SafeWriterError(E_IO, "temp content does not match requested bytes")
            temp.facts = facts
            return temp, expected_sha
        except OSError as exc:
            retained = not self._cleanup_temp(temp)
            suffix = f"; temp retained as {temp.name!r}" if retained else ""
            raise SafeWriterError(E_IO, f"temp write/readback failed: {exc}{suffix}") from exc
        except Exception as exc:
            # The temp was created by us and has not been committed.  There is
            # no target change to preserve, so mark it delete-on-close.  If the
            # mark itself fails, name it in the failure instead of leaving an
            # untracked orphan.
            retained = not self._cleanup_temp(temp)
            if retained:
                raise SafeWriterError(
                    E_IO, f"temp failure ({exc}); temp retained as {temp.name!r}"
                ) from exc
            raise

    def _rename(self, handle: int, parent: DirectoryAnchor, name: str, *, context: str) -> None:
        status = native.rename(handle, parent.handle, name, replace=False)
        if status == native.STATUS_SUCCESS:
            return
        if status == native.STATUS_OBJECT_NAME_COLLISION:
            raise SafeWriterError(E_TARGET_EXISTS, f"{context}: target name already exists")
        _fail_status(status, context, default=E_COMMIT_UNCERTAIN)

    def _cleanup_temp(self, temp: TargetAnchor | None) -> bool:
        """Mark a temp for delete-on-close and release its handle.

        Returns True when the disposition was successfully set.  A False result
        means the caller must surface the temp name in the structured failure;
        it must not silently claim the temp was removed.
        """
        if temp is None:
            return True
        marked = False
        try:
            native.delete_on_close(temp.handle)
            marked = True
        except Exception:
            pass
        temp.close()
        return marked

    def _append(self, session: _Session, ref: RequestRef, state: str, action: str, **fields: Any) -> None:
        session.journal.append(op_id=ref.op_id, state=state, action=action, **fields)

    def _maybe_fault(self, stage: str, **info: Any) -> None:
        if self.fault_hook is not None:
            self.fault_hook(stage, info)

    def _journal_path(self, session: _Session, ref: RequestRef) -> str:
        return ntpath.join(session.root.root_path, "creation", "operations", f"{ref.batch_id}.journal.ndjson")

    # ------------------------------------------------------------------ results
    def _plain_refusal(self, ref: Any, action: str, exc: BaseException) -> WriteResult:
        code = getattr(exc, "code", E_OPERATION_INVALID)
        message = str(exc) or type(exc).__name__
        op_id = getattr(ref, "op_id", "unknown")
        return WriteResult(
            status=REFUSED,
            operation_id=op_id,
            action=action,
            code=code,
            message=message,
            durable=False,
        )

    def _plain_session_failure(self, ref: RequestRef, action: str, exc: SafeWriterError) -> WriteResult:
        status = REFUSED if exc.code in _PREFLIGHT_CODES else FAILED
        return WriteResult(
            status=status,
            operation_id=ref.op_id,
            action=action,
            code=exc.code,
            message=str(exc),
            durable=False,
            details=exc.details,
        )

    def _finish_failure(
        self,
        session: _Session,
        ref: RequestRef,
        action: str,
        exc: BaseException,
        *,
        status: str,
    ) -> WriteResult:
        code = getattr(exc, "code", E_IO)
        message = str(exc) or type(exc).__name__
        journal_failed = False
        try:
            self._append(session, ref, "FAILED", "OPERATION_FAILED", error_code=code, error_message=message)
        except Exception:
            journal_failed = True
        return WriteResult(
            status=status,
            operation_id=ref.op_id,
            action=action,
            code=code,
            message=message,
            journal_path=self._journal_path(session, ref),
            durable=False,
            details={"journal_recorded": not journal_failed},
        )

    def _uncertain(
        self,
        session: _Session,
        ref: RequestRef,
        action: str,
        exc: BaseException,
        *,
        temp: TargetAnchor | None,
        target_name: str,
        quarantine: str | None = None,
    ) -> WriteResult:
        code = getattr(exc, "code", E_COMMIT_UNCERTAIN)
        message = str(exc) or type(exc).__name__
        journal_failed = False
        try:
            self._append(
                session,
                ref,
                "UNCERTAIN",
                "COMMIT_UNCERTAIN",
                error_code=code,
                error_message=message,
                target_name=target_name,
                quarantine=quarantine,
            )
        except Exception:
            journal_failed = True
        return WriteResult(
            status=UNCERTAIN,
            operation_id=ref.op_id,
            action=action,
            code=code,
            message=message,
            temp_name=temp.name if temp is not None else None,
            quarantine_name=quarantine,
            journal_path=self._journal_path(session, ref),
            durable=False,
            details={"journal_recorded": not journal_failed, "target_name": target_name},
        )
