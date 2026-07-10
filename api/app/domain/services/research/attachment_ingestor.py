import hashlib
from collections.abc import Callable

from bs4 import BeautifulSoup
from markdownify import markdownify
from starlette.concurrency import run_in_threadpool

from app.domain.external.file_storage import FileStorage
from app.domain.models.research import (
    AttachmentIngestResult,
    AttachmentIssue,
    EvidenceExcerpt,
    ResearchSource,
)
from app.domain.repositories.uow import IUnitOfWork


ALLOWED_ATTACHMENT_TYPES = {
    "text/plain",
    "text/markdown",
    "text/html",
}
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024


class AttachmentIngestor:
    def __init__(
            self,
            file_storage: FileStorage,
            uow_factory: Callable[[], IUnitOfWork],
    ) -> None:
        self._file_storage = file_storage
        self._uow_factory = uow_factory

    async def ingest(
            self,
            run_id: str,
            attachment_ids: list[str],
    ) -> AttachmentIngestResult:
        result = AttachmentIngestResult()
        for file_id in attachment_ids:
            file_data, file = await self._file_storage.download_file(file_id)
            content_type = file.mime_type.split(";", 1)[0].lower()
            if content_type not in ALLOWED_ATTACHMENT_TYPES:
                result.issues.append(AttachmentIssue(
                    file_id=file_id,
                    code="unsupported_content_type",
                    message=f"unsupported attachment content type: {content_type}",
                ))
                continue
            if file.size > MAX_ATTACHMENT_BYTES:
                result.issues.append(AttachmentIssue(
                    file_id=file_id,
                    code="content_too_large",
                    message="attachment exceeds 2 MiB",
                ))
                continue

            raw_content = await run_in_threadpool(file_data.read)
            if len(raw_content) > MAX_ATTACHMENT_BYTES:
                result.issues.append(AttachmentIssue(
                    file_id=file_id,
                    code="content_too_large",
                    message="attachment exceeds 2 MiB",
                ))
                continue
            text = self._extract_text(raw_content, content_type)
            content_hash = hashlib.sha256(raw_content).hexdigest()
            source = ResearchSource(
                run_id=run_id,
                canonical_url=f"attachment://{file.id}",
                original_url=f"attachment://{file.id}",
                title=file.filename,
                domain="attachment",
                content_type=content_type,
                content_hash=content_hash,
                object_storage_key=file.key,
                source_class="primary",
                metadata={"file_id": file.id, "filename": file.filename},
            )
            excerpt = text[:2_000].strip()
            if not excerpt:
                result.issues.append(AttachmentIssue(
                    file_id=file_id,
                    code="empty_content",
                    message="attachment contains no readable text",
                ))
                continue
            evidence = EvidenceExcerpt(
                source_id=source.id,
                run_id=run_id,
                locator=f"file:{file.filename}:1",
                excerpt=excerpt,
                excerpt_hash=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            )
            async with self._uow_factory() as uow:
                await uow.research.add_source(source)
                await uow.research.add_evidence(evidence)
            result.sources.append(source)
            result.evidence.append(evidence)
        return result

    @staticmethod
    def _extract_text(raw_content: bytes, content_type: str) -> str:
        decoded = raw_content.decode("utf-8", errors="replace")
        if content_type != "text/html":
            return decoded.strip()
        soup = BeautifulSoup(decoded, "html.parser")
        for element in soup.select("script, style, nav, noscript, iframe"):
            element.decompose()
        return markdownify(
            str(soup.body or soup),
            heading_style="ATX",
        ).strip()
