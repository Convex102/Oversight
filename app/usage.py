import time, datetime as dt
from sqlalchemy.orm import Session
from app.db.models import UsageRecord
from app.config import settings

def _period_ym(now=None):
    now = now or dt.datetime.utcnow()
    return now.strftime("%Y-%m")

def get_or_create_usage(db: Session, tenant_id: int, period_ym: str | None=None) -> UsageRecord:
    period_ym = period_ym or _period_ym()
    row = db.query(UsageRecord).filter(UsageRecord.tenant_id==tenant_id, UsageRecord.period_ym==period_ym).first()
    if not row:
        row = UsageRecord(tenant_id=tenant_id, period_ym=period_ym, packs_processed=0, compute_seconds=0)
        db.add(row); db.commit(); db.refresh(row)
    return row

def pack_limit_for_plan(plan: str) -> int:
    return settings.PRO_MONTHLY_PACK_LIMIT if plan=="pro" else settings.BASIC_MONTHLY_PACK_LIMIT

def assert_within_limits(db: Session, tenant_id: int, plan: str):
    row = get_or_create_usage(db, tenant_id)
    if row.packs_processed >= pack_limit_for_plan(plan):
        # In production you'd allow overage and bill; here we block by default.
        raise Exception("Pack limit exceeded for current period")

class Timer:
    def __enter__(self):
        self.t0 = time.time()
        return self
    def __exit__(self, exc_type, exc, tb):
        self.seconds = int(time.time() - self.t0)
