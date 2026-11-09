"""Non-streaming clinical responses retain authorization locks through ASGI handoff."""

import asyncio
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.api.dependencies import CurrentAccount, Database
from app.api.v1.auth import PrivateValidationRoute
from app.schemas.clinical_fact import CorrectFact, CreateFact, FactResponse
from app.services.clinical_facts import (
    accepted_facts,
    fact_history,
    lock_patient_scope,
    structured_entry,
)

router = APIRouter(
    prefix="/patients/{patient_id}/facts",
    tags=["clinical facts"],
    route_class=PrivateValidationRoute,
)


class ClinicalResponse(JSONResponse):
    """Never release a prepared clinical body using an earlier permission snapshot.

    Writes have already committed before this response is constructed. Reacquire
    the shared patient gate and reauthorize before handing the body to ASGI. A
    revoke in that gap suppresses the acknowledgement, not the committed write.
    Reads never drop their original gate. No streaming or external/LLM work here.
    """

    def __init__(
        self,
        db: AsyncSession,
        account_id: UUID,
        patient_id: UUID,
        content: Any,
        *,
        status_code: int = 200,
    ) -> None:
        self.db, self.account_id, self.patient_id = db, account_id, patient_id
        super().__init__(
            content=jsonable_encoder(content),
            status_code=status_code,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await lock_patient_scope(self.db, self.account_id, self.patient_id)
            # Bound the non-streaming handoff: no locks across future LLM calls.
            async with asyncio.timeout(5):
                await super().__call__(scope, receive, send)
            # Release the read-only gate only after the last ASGI body send.
            await self.db.commit()
        except BaseException:
            await self.db.rollback()
            raise


@router.post("", response_model=FactResponse, status_code=201)
async def create(
    patient_id: UUID, payload: CreateFact, db: Database, account: CurrentAccount
) -> ClinicalResponse:
    result = await structured_entry(db, account, patient_id, payload)
    return ClinicalResponse(db, account.id, patient_id, result, status_code=201)


@router.get("", response_model=list[FactResponse])
async def current(patient_id: UUID, db: Database, account: CurrentAccount) -> ClinicalResponse:
    result = await accepted_facts(db, account.id, patient_id)
    return ClinicalResponse(db, account.id, patient_id, result)


@router.get("/{fact_id}/history", response_model=list[FactResponse])
async def history(
    patient_id: UUID, fact_id: UUID, db: Database, account: CurrentAccount
) -> ClinicalResponse:
    result = await fact_history(db, account.id, patient_id, fact_id)
    return ClinicalResponse(db, account.id, patient_id, result)


@router.post("/{fact_id}/corrections", response_model=FactResponse, status_code=201)
async def correct(
    patient_id: UUID, fact_id: UUID, payload: CorrectFact, db: Database, account: CurrentAccount
) -> ClinicalResponse:
    result = await structured_entry(db, account, patient_id, payload, fact_id=fact_id)
    return ClinicalResponse(db, account.id, patient_id, result, status_code=201)
