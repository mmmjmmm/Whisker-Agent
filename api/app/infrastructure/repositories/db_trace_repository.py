from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.trace import TraceSpan
from app.domain.repositories.trace_repository import TraceRepository
from app.infrastructure.models import TraceSpanModel


class DBTraceRepository(TraceRepository):
    """基于 Postgres 的 Trace 仓库。"""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def create_span(self, span: TraceSpan) -> None:
        self.db_session.add(TraceSpanModel.from_domain(span))

    async def finish_span(self, span: TraceSpan) -> None:
        stmt = select(TraceSpanModel).where(TraceSpanModel.id == span.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            self.db_session.add(TraceSpanModel.from_domain(span))
            return
        record.update_from_domain(span)

    async def list_by_session(self, session_id: str) -> list[TraceSpan]:
        stmt = (
            select(TraceSpanModel)
            .where(TraceSpanModel.session_id == session_id)
            .order_by(TraceSpanModel.started_at.asc(), TraceSpanModel.id.asc())
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def list_by_trace(
        self,
        session_id: str,
        trace_id: str,
    ) -> list[TraceSpan]:
        stmt = (
            select(TraceSpanModel)
            .where(
                TraceSpanModel.session_id == session_id,
                TraceSpanModel.trace_id == trace_id,
            )
            .order_by(TraceSpanModel.started_at.asc(), TraceSpanModel.id.asc())
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]
