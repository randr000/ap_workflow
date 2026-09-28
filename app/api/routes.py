"""HTTP API — a thin layer over APWorkflowService (the same controls as the UI).

    uvicorn app.api.routes:app --reload

Authentication is out of scope for the PoC: the acting demo user is passed by
name and resolved to its roles; permissions are still enforced by the service.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.domain.approval import DEMO_USERS, User, get_user
from app.services.reporting import dashboard_metrics
from app.services.workflow_service import (
    APWorkflowService,
    PostingBlockedError,
    WorkflowError,
    create_workflow_service,
)


@lru_cache
def get_service() -> APWorkflowService:
    return create_workflow_service()


def _user(name: str) -> User:
    try:
        return get_user(name)
    except KeyError:
        raise HTTPException(400, f"Unknown user '{name}'. Demo users: {[u.name for u in DEMO_USERS]}")


class DocumentIn(BaseModel):
    document: str
    source: str = "api"


class ActionIn(BaseModel):
    user: str
    comment: str | None = None


class RejectIn(BaseModel):
    user: str
    reason: str


class CodingIn(BaseModel):
    user: str
    gl_account: str
    department: str
    location: str


router = APIRouter()
Svc = Depends(get_service)


@router.get("/health")
def health(svc: APWorkflowService = Svc):
    return {"status": "ok", "ai_provider": svc.provider.name}


@router.get("/invoices")
def list_invoices(svc: APWorkflowService = Svc):
    return [{"id": c.id, "invoice_number": c.invoice.invoice_number, "vendor": c.invoice.vendor_name,
             "total": str(c.invoice.total), "status": c.status,
             "open_exceptions": [e.type for e in c.open_exceptions]} for c in svc.list()]


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: str, svc: APWorkflowService = Svc):
    return svc.get(invoice_id).model_dump(mode="json")


@router.post("/invoices", status_code=201)
def process_invoice(body: DocumentIn, svc: APWorkflowService = Svc):
    return svc.process_document(body.document, body.source).model_dump(mode="json")


@router.post("/invoices/{invoice_id}/approve")
def approve(invoice_id: str, body: ActionIn, svc: APWorkflowService = Svc):
    return svc.approve(invoice_id, _user(body.user), body.comment).model_dump(mode="json")


@router.post("/invoices/{invoice_id}/reject")
def reject(invoice_id: str, body: RejectIn, svc: APWorkflowService = Svc):
    return svc.reject(invoice_id, _user(body.user), body.reason).model_dump(mode="json")


@router.post("/invoices/{invoice_id}/coding")
def correct_coding(invoice_id: str, body: CodingIn, svc: APWorkflowService = Svc):
    return svc.correct_coding(invoice_id, _user(body.user), body.gl_account, body.department,
                              body.location).model_dump(mode="json")


@router.post("/invoices/{invoice_id}/exceptions/{exception_id}/dismiss")
def dismiss(invoice_id: str, exception_id: str, body: RejectIn, svc: APWorkflowService = Svc):
    return svc.dismiss_exception(invoice_id, exception_id, _user(body.user), body.reason).model_dump(mode="json")


@router.post("/invoices/{invoice_id}/post")
def post(invoice_id: str, body: ActionIn, svc: APWorkflowService = Svc):
    return svc.post(invoice_id, _user(body.user)).model_dump(mode="json")


@router.get("/exceptions")
def exceptions(status: str | None = "OPEN", svc: APWorkflowService = Svc):
    return [e.model_dump(mode="json") | {"vendor": c.invoice.vendor_name, "amount": str(c.invoice.total)}
            for c in svc.list() for e in c.exceptions if status is None or e.status == status]


@router.get("/audit")
def audit(invoice_id: str | None = None, svc: APWorkflowService = Svc):
    return [e.model_dump(mode="json") for e in svc.repo.list_audit(invoice_id)]


@router.get("/metrics")
def metrics(svc: APWorkflowService = Svc):
    m = dashboard_metrics(svc.list(), svc.repo.list_ledger())
    return {k: (str(v) if k in ("posted_amount", "total_amount") else v) for k, v in m.items()}


def create_app() -> FastAPI:
    api = FastAPI(title="AI AP Automation Agent", version="0.1.0",
                  description="AI proposes and reasons. Deterministic application logic validates and controls.")
    api.include_router(router)

    @api.exception_handler(PostingBlockedError)
    def _blocked(_, exc: PostingBlockedError):
        failures = [c.model_dump() for c in exc.report.failures] if exc.report else []
        return JSONResponse(status_code=409, content={"detail": str(exc), "failed_checks": failures})

    @api.exception_handler(WorkflowError)
    def _workflow(_, exc: WorkflowError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @api.exception_handler(KeyError)
    def _missing(_, exc: KeyError):
        return JSONResponse(status_code=404, content={"detail": str(exc).strip("'")})

    return api


app = create_app()
