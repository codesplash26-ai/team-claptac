"""Assemble the control boundary and its executors onto a Runtime.

    control = attach_control(runtime)       # before ``await runtime.start()``
    control.start_worker()                  # inside a running event loop

The Gateway subscribes to PLAN_PROPOSED on the bus. A8 and A9 are not bus subscribers:
the Gateway calls them as executors once an action is authorised.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from atlas.agents.a8_dispatch import Dispatch
from atlas.agents.a9_comms import Comms
from atlas.control.action_gateway import Gateway
from atlas.control.approvals import ApprovalQueue
from atlas.control.policy_engine import PolicyEngine

log = logging.getLogger(__name__)


@dataclass
class ControlPlane:
    runtime: object
    policy: PolicyEngine
    approvals: ApprovalQueue
    gateway: Gateway
    dispatch: Dispatch
    comms: Comms
    _task: asyncio.Task | None = field(default=None, repr=False)

    def start_worker(self, interval_seconds: float = 0.5) -> None:
        """Background loop: execute authorised actions, flag unacknowledged missions."""
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop(interval_seconds))

    async def stop_worker(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def tick(self) -> None:
        await self.gateway.run_pending()
        for event in self.dispatch.check_overdue():
            await self.runtime.bus.publish(event)

    async def _loop(self, interval: float) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("control worker tick failed")
            await asyncio.sleep(interval)


def attach_control(
    runtime, *, policy_path=None, policy=None, channel=None, translator=None
) -> ControlPlane:
    policy = policy or PolicyEngine.from_file(policy_path)
    approvals = ApprovalQueue(runtime.db)
    gateway = Gateway(
        runtime.db,
        runtime.ledger,
        policy,
        approvals,
        cop=runtime.cop,
        publish=runtime.bus.publish,
        clock=runtime.clock,
    )
    dispatch = Dispatch(runtime.bus, runtime.cop, runtime.ledger, clock=runtime.clock)
    comms = Comms(
        runtime.bus,
        runtime.cop,
        runtime.ledger,
        clock=runtime.clock,
        channel=channel,
        translator=translator,
    )
    gateway.is_suspended = lambda name: name in runtime.bus.suspended
    gateway.register_executor(dispatch.name, dispatch)
    gateway.register_executor(comms.name, comms)
    runtime.bus.subscribe(gateway.name, gateway.subscribes_to, gateway.handle_plan)
    return ControlPlane(runtime, policy, approvals, gateway, dispatch, comms)
