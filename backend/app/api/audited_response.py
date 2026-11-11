"""Internal one-shot non-streaming adapter. No production route is registered."""

import asyncio
import math
from collections.abc import Callable
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse, Response
from starlette.types import Receive, Scope, Send

from app.schemas.clinical_summary import ClinicalSummaryReleaseRequest
from app.schemas.evidence_aware_draft import EvidenceAwareDraftRequest
from app.services.clinical_summary_release import (
    prepare_summary_release,
    revalidate_summary_release,
)
from app.services.evidence_aware_evaluator import catalogue_fingerprint
from app.services.response_release import (
    _Prepared,
    prepare_release,
    revalidate_release,
    transaction_identity,
)

MAX_HANDOFF_TIMEOUT = 5.0


def _require_fresh(db: AsyncSession) -> None:
    if not db.is_active or db.in_transaction() or db.new or db.dirty or db.deleted:
        raise HTTPException(503, "Dedicated fresh release session required")


async def _stop_owned(*tasks: asyncio.Task[None]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    cleanup = asyncio.gather(*tasks, return_exceptions=True)
    interrupted = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            interrupted = True
    if interrupted:
        raise asyncio.CancelledError()


class AuditedRecordResponse(Response):
    """Own two dedicated sessions. Constructor arguments are trusted orchestration.

    No existing receipt, body, headers, PASS or permit is accepted. A new
    instance cannot resume an old intent. Validation is not authentication.
    """

    def __init__(
        self,
        actor_id: UUID,
        patient_id: UUID,
        original_request: object,
        operation_id: UUID,
        session_factory: Callable[[], AsyncSession],
        *,
        handoff_timeout: float = 5.0,
        include_provenance: bool = False,
        clinical_summary: bool = False,
    ) -> None:
        super().__init__(content=b"")  # Its body/headers are NEVER used for sending.
        if type(include_provenance) is not bool:
            raise ValueError("Invalid provenance release mode")
        if type(clinical_summary) is not bool or (clinical_summary and include_provenance):
            raise ValueError("Invalid summary release mode")
        self._include_provenance = include_provenance
        if (
            isinstance(handoff_timeout, bool)
            or not math.isfinite(handoff_timeout)
            or not 0 < handoff_timeout <= MAX_HANDOFF_TIMEOUT
        ):
            raise ValueError("Handoff timeout must be positive and at most five seconds")
        self._request: EvidenceAwareDraftRequest | None = None
        self._summary_request: ClinicalSummaryReleaseRequest | None = None
        try:
            if clinical_summary:
                self._summary_request = ClinicalSummaryReleaseRequest.model_validate(
                    original_request.model_dump(warnings=False)
                    if isinstance(original_request, ClinicalSummaryReleaseRequest)
                    else original_request
                )
            else:
                self._request = EvidenceAwareDraftRequest.model_validate(
                    original_request.model_dump(warnings=False)
                    if isinstance(original_request, EvidenceAwareDraftRequest)
                    else original_request
                )
        except ValidationError:
            raise HTTPException(422, "Invalid audited release request") from None
        self._actor, self._patient, self._operation = actor_id, patient_id, operation_id
        self._factory, self._timeout, self._used = session_factory, handoff_timeout, False

    async def _prepare(self, db: AsyncSession) -> _Prepared:
        if self._summary_request is not None:
            return await prepare_summary_release(
                db, self._actor, self._patient, self._summary_request, self._operation
            )
        assert self._request is not None
        if self._include_provenance:
            return await prepare_release(
                db,
                self._actor,
                self._patient,
                self._request,
                self._operation,
                include_provenance=True,
            )
        return await prepare_release(db, self._actor, self._patient, self._request, self._operation)

    async def _revalidate(self, db: AsyncSession, prepared: _Prepared) -> tuple[bytes, str]:
        if self._summary_request is not None:
            return await revalidate_summary_release(
                db, self._actor, self._patient, self._summary_request, self._operation, prepared
            )
        assert self._request is not None
        if self._include_provenance:
            return await revalidate_release(
                db,
                self._actor,
                self._patient,
                self._request,
                prepared,
                include_provenance=True,
            )
        return await revalidate_release(db, self._actor, self._patient, self._request, prepared)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._used:
            raise HTTPException(409, "Release response instance already used")
        self._used = True  # Set before the first await; invocation cannot be raced/reused.
        disconnected = asyncio.Event()
        started = False

        async def watch_disconnect() -> None:
            try:
                while True:
                    if (await receive())["type"] == "http.disconnect":
                        disconnected.set()
                        return
                    await asyncio.sleep(0)
            except OSError:
                disconnected.set()

        watcher = asyncio.create_task(watch_disconnect())
        try:
            async with self._factory() as db:
                first_session = db
                try:
                    _require_fresh(db)
                    prepared = await self._prepare(db)
                    if disconnected.is_set():
                        raise asyncio.CancelledError()
                    await (
                        db.commit()
                    )  # Evaluation + intent atomic; failure => zero protected sends.
                except BaseException:
                    await db.rollback()
                    raise
            if prepared.intent is None:
                raise HTTPException(409, "Verified response withheld")
            async with self._factory() as db:
                try:
                    _require_fresh(db)
                    if db is first_session:
                        raise HTTPException(503, "Distinct release sessions required")
                    body, fingerprint = await self._revalidate(db, prepared)
                    identity = await transaction_identity(db)
                    check_lock = asyncio.Lock()

                    async def check() -> None:
                        async with check_lock:
                            if await transaction_identity(db) != identity:
                                raise HTTPException(503, "Release transaction changed")
                        if disconnected.is_set() or watcher.done():
                            raise HTTPException(503, "Release connection unavailable")
                        if catalogue_fingerprint() != fingerprint:
                            raise HTTPException(503, "Release catalogue changed")

                    async def transmit() -> None:
                        nonlocal started
                        await check()
                        started = True  # Conservative: send may partially succeed before raising.
                        await send(
                            {
                                "type": "http.response.start",
                                "status": 200,
                                "headers": [
                                    (b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode("ascii")),
                                    (b"cache-control", b"no-store"),
                                    (b"referrer-policy", b"no-referrer"),
                                ],
                            }
                        )
                        await check()
                        await send({"type": "http.response.body", "body": body, "more_body": False})

                    async def monitor() -> None:
                        while True:
                            await asyncio.sleep(0.025)
                            await check()

                    sending, monitoring = (
                        asyncio.create_task(transmit()),
                        asyncio.create_task(monitor()),
                    )
                    try:
                        async with asyncio.timeout(self._timeout):
                            done, _ = await asyncio.wait(
                                {sending, monitoring, watcher}, return_when=asyncio.FIRST_COMPLETED
                            )
                            if sending in done:
                                await sending
                            elif monitoring in done:
                                await monitoring
                            else:
                                raise HTTPException(503, "Release connection unavailable")
                    finally:
                        # Never release DB protections while owned send/check work remains.
                        await _stop_owned(sending, monitoring)
                finally:
                    await db.rollback()  # Read-only T2; no post-send audit write/commit.
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not started and not disconnected.is_set():
                status = error.status_code if isinstance(error, HTTPException) else 503
                # No exception values, identifiers, draft text or clinical records.
                await JSONResponse(
                    {"detail": "Audited response unavailable"},
                    status_code=status,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
            # Once start may have been sent: never emit a second response or retry.
        finally:
            await _stop_owned(watcher)
