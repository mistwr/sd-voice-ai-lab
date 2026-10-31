"""Isolated Ricardo recovery entrypoint; never registers as production lumin-web."""
import os
from livekit.agents import WorkerOptions, cli
import web_agent

async def recovery_entrypoint(ctx):
    # The historical voice worker retains its original metadata-driven pipeline.
    # Require a deliberate recovery profile; never silently run Piper.
    import json
    metadata = json.loads(ctx.job.metadata or "{}")
    profile = metadata.get("agentProfile") or {}
    if profile.get("voiceEngine") not in ("xai", "neural-ptpt"):
        raise ValueError("Recovery test requires agentProfile.voiceEngine=xai")
    await web_agent.entrypoint(ctx)

if __name__ == "__main__":
    cli.run_app(WorkerOptions(
        entrypoint_fnc=recovery_entrypoint,
        agent_name="lumin-ricardo-recovery",
        prewarm_fnc=web_agent.prewarm,
        num_idle_processes=1,
        initialize_process_timeout=30.0,
        job_memory_warn_mb=900,
    ))
