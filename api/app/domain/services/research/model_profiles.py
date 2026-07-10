from pydantic import BaseModel


class ResearchModelProfiles(BaseModel):
    planner: str = "default"
    worker: str = "default"
    reviewer: str = "default"
    synthesizer: str = "default"
    citation_verifier: str = "default"

