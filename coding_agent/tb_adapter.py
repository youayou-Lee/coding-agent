"""coding_agent v0.1: Terminal-Bench adapter.

tb run --agent-import-path course.coding_agent.tb_adapter:CodingAgentTB
"""

from pathlib import Path
from typing import Optional

from terminal_bench.agents.base_agent import AgentResult, BaseAgent
from terminal_bench.agents.failure_mode import FailureMode
from terminal_bench.terminal.tmux_session import TmuxSession

from coding_agent.agent import make_coding_agent
from coding_agent.logging_util import RunLogger
from coding_agent.terminal import TmuxBackend


class CodingAgentTB(BaseAgent):
    @staticmethod
    def name() -> str:
        return "agno-coding-agent"

    def perform_task(
        self,
        instruction: str,
        session: TmuxSession,
        logging_dir: Optional[Path] = None,
    ) -> AgentResult:
        log_dir = Path(logging_dir) if logging_dir else Path("/tmp/coding_agent_logs")
        logger = RunLogger(log_dir)
        backend = TmuxBackend(session, logger=logger)
        agent = make_coding_agent(backend, logger, workdir=Path("/app"))

        logger.event("task_start", instruction_preview=instruction[:200])
        response = agent.run(instruction, stream=False)
        text = getattr(response, "content", "") or str(response)
        logger.final("finished", response_preview=text[:300])

        return AgentResult(
            total_input_tokens=0,
            total_output_tokens=0,
            failure_mode=FailureMode.NONE,
        )
