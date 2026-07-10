from io import BytesIO

from app.domain.models.file import File
from app.domain.services.research.attachment_ingestor import AttachmentIngestor


class FakeFileStorage:
    def __init__(self, files: dict[str, tuple[bytes, File]]) -> None:
        self.files = files

    async def download_file(self, file_id: str):
        content, file = self.files[file_id]
        return BytesIO(content), file


class FakeResearchRepository:
    def __init__(self) -> None:
        self.sources = []
        self.evidence = []

    async def add_source(self, source) -> None:
        self.sources.append(source)

    async def add_evidence(self, evidence) -> None:
        self.evidence.append(evidence)


class FakeUow:
    def __init__(self, repository) -> None:
        self.research = repository

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


async def test_attachment_ingestor_reads_text_without_sandbox() -> None:
    file = File(
        id="file-1",
        filename="notes.md",
        key="uploads/notes.md",
        mime_type="text/markdown",
        size=12,
    )
    repository = FakeResearchRepository()
    ingestor = AttachmentIngestor(
        file_storage=FakeFileStorage({"file-1": (b"# Notes\nFact", file)}),
        uow_factory=lambda: FakeUow(repository),
    )

    result = await ingestor.ingest("run-1", ["file-1"])

    assert result.issues == []
    assert result.sources[0].canonical_url == "attachment://file-1"
    assert "Fact" in result.evidence[0].excerpt
    assert repository.sources and repository.evidence


async def test_attachment_ingestor_reports_unsupported_mime() -> None:
    file = File(
        id="file-2",
        filename="report.pdf",
        key="uploads/report.pdf",
        mime_type="application/pdf",
        size=3,
    )
    repository = FakeResearchRepository()
    ingestor = AttachmentIngestor(
        file_storage=FakeFileStorage({"file-2": (b"pdf", file)}),
        uow_factory=lambda: FakeUow(repository),
    )

    result = await ingestor.ingest("run-1", ["file-2"])

    assert result.sources == []
    assert result.issues[0].code == "unsupported_content_type"
    assert repository.sources == []
