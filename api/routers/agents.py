from datetime import datetime
from fastapi import APIRouter
from api.models.schemas import AgentStatus

router = APIRouter()

# In-memory agent registry — updated by the orchestrator during pipeline runs
_agent_registry: dict[str, AgentStatus] = {
    "Analyzer": AgentStatus(name="Analyzer", status="idle", last_run=datetime.utcnow()),
    "UnitGenerator": AgentStatus(name="UnitGenerator", status="idle", last_run=datetime.utcnow()),
    "IntegrationGenerator": AgentStatus(name="IntegrationGenerator", status="idle", last_run=datetime.utcnow()),
    "Debugger": AgentStatus(name="Debugger", status="idle", last_run=datetime.utcnow()),
}


@router.get("/agents/status", response_model=list[AgentStatus])
async def get_agent_status():
    return list(_agent_registry.values())


def update_agent(name: str, status: str, current_task: str | None = None):
    """Called by orchestrator nodes to update agent state."""
    if name in _agent_registry:
        _agent_registry[name] = AgentStatus(
            name=name,
            status=status,  # type: ignore[arg-type]
            last_run=datetime.utcnow(),
            current_task=current_task,
        )
