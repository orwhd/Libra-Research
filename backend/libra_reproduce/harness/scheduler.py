from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal

from ..config import DEFAULT_LIMITS, HarnessResourceLimits

LeaseKind = Literal["agent_task", "experiment"]
AcquireOutcome = Literal["granted", "queued", "denied"]


@dataclass
class ResourceLease:
    lease_id: str
    run_id: str
    kind: LeaseKind
    owner_id: str
    is_exploration: bool
    budget_fraction: float = 0.0


class ResourceDeniedError(RuntimeError):
    """Raised when a resource request exceeds hard policy or exploration budget limits."""


class ResourceScheduler:
    """
    Server-enforced dual-pool scheduler (L1.5 / L3 §16 / HAR-01 / HAR-02):
    - Pool 1: `max_active_agent_tasks` (concurrent active agent task leases, default 4)
    - Pool 2: `max_parallel_experiments` (simultaneously executing Python compute experiments, default 2)
    - Exploration cap: `exploration_budget_fraction` (default 0.20) so exploratory Pods never starve main paper reproduction.
    Waiting queue entries do NOT occupy active slots; failure/cancel/exit always releases leases in `finally`.
    """

    def __init__(self, limits: HarnessResourceLimits = DEFAULT_LIMITS) -> None:
        self.limits = limits
        self._lock = asyncio.Lock()
        self._agent_cond = asyncio.Condition(self._lock)
        self._exp_cond = asyncio.Condition(self._lock)

        self._active_agent_leases: dict[str, ResourceLease] = {}
        self._queued_agent_waiters: list[str] = []

        self._active_exp_leases: dict[str, ResourceLease] = {}
        self._queued_exp_waiters: list[str] = []

        self._exploration_spent_by_run: dict[str, float] = {}

    def snapshot_state(self, run_id: str | None = None) -> dict[str, Any]:
        exp_used = self._exploration_spent_by_run.get(run_id or "", 0.0)
        return {
            "limits": {
                "max_active_agent_tasks": self.limits.max_active_agent_tasks,
                "max_parallel_experiments": self.limits.max_parallel_experiments,
                "max_subagent_depth": self.limits.max_subagent_depth,
                "max_debate_turns": self.limits.max_debate_turns,
                "exploration_budget_fraction": self.limits.exploration_budget_fraction,
            },
            "active_agent_tasks": len(self._active_agent_leases),
            "queued_agent_tasks": len(self._queued_agent_waiters),
            "active_experiments": len(self._active_exp_leases),
            "queued_experiments": len(self._queued_exp_waiters),
            "exploration_used_fraction": round(exp_used, 4),
        }

    def check_subagent_depth(self, depth: int) -> None:
        if depth > self.limits.max_subagent_depth:
            raise ResourceDeniedError(
                f"Subagent depth {depth} exceeds max_subagent_depth={self.limits.max_subagent_depth}."
            )

    async def try_acquire_agent_task(
        self,
        *,
        run_id: str,
        owner_id: str,
        is_exploration: bool = False,
        budget_fraction: float = 0.0,
        wait_if_full: bool = True,
        on_queued: Any | None = None,
    ) -> tuple[ResourceLease | None, AcquireOutcome, str]:
        async with self._agent_cond:
            if is_exploration:
                used = self._exploration_spent_by_run.get(run_id, 0.0)
                if used + budget_fraction > self.limits.exploration_budget_fraction + 1e-9:
                    return (
                        None,
                        "denied",
                        f"Exploration budget request ({budget_fraction:.2f}) exceeds remaining cap "
                        f"({self.limits.exploration_budget_fraction - used:.2f}).",
                    )
                # Prevent exploration tasks from taking the very last slot needed by main tasks
                if (
                    self.limits.max_active_agent_tasks > 1
                    and len(self._active_agent_leases) >= self.limits.max_active_agent_tasks - 1
                    and not wait_if_full
                ):
                    return (None, "queued", "Reserved final agent task slot for primary reproduction task.")

            was_queued = False
            if len(self._active_agent_leases) >= self.limits.max_active_agent_tasks:
                if not wait_if_full:
                    return (
                        None,
                        "queued",
                        f"Agent task pool full ({len(self._active_agent_leases)}/{self.limits.max_active_agent_tasks}).",
                    )
                was_queued = True
                self._queued_agent_waiters.append(owner_id)
                if on_queued is not None:
                    on_queued(self.snapshot_state(run_id))
                try:
                    while len(self._active_agent_leases) >= self.limits.max_active_agent_tasks:
                        await self._agent_cond.wait()
                finally:
                    if owner_id in self._queued_agent_waiters:
                        self._queued_agent_waiters.remove(owner_id)

            lease = ResourceLease(
                lease_id=f"lease_ag_{uuid.uuid4().hex[:10]}",
                run_id=run_id,
                kind="agent_task",
                owner_id=owner_id,
                is_exploration=is_exploration,
                budget_fraction=budget_fraction,
            )
            self._active_agent_leases[lease.lease_id] = lease
            if is_exploration and budget_fraction > 0:
                self._exploration_spent_by_run[run_id] = (
                    self._exploration_spent_by_run.get(run_id, 0.0) + budget_fraction
                )
            return lease, ("queued" if was_queued else "granted"), "Agent task slot leased."

    async def release_agent_task(self, lease_id: str) -> None:
        async with self._agent_cond:
            self._active_agent_leases.pop(lease_id, None)
            self._agent_cond.notify_all()

    @asynccontextmanager
    async def agent_task_slot(
        self,
        *,
        run_id: str,
        owner_id: str,
        is_exploration: bool = False,
        budget_fraction: float = 0.0,
        on_queued: Any | None = None,
    ) -> AsyncIterator[tuple[ResourceLease, AcquireOutcome]]:
        lease, outcome, reason = await self.try_acquire_agent_task(
            run_id=run_id,
            owner_id=owner_id,
            is_exploration=is_exploration,
            budget_fraction=budget_fraction,
            wait_if_full=True,
            on_queued=on_queued,
        )
        if lease is None:
            raise ResourceDeniedError(reason)
        try:
            yield lease, outcome
        finally:
            await self.release_agent_task(lease.lease_id)

    async def try_acquire_experiment(
        self,
        *,
        run_id: str,
        owner_id: str,
        wait_if_full: bool = True,
        on_queued: Any | None = None,
    ) -> tuple[ResourceLease | None, AcquireOutcome, str]:
        async with self._exp_cond:
            was_queued = False
            if len(self._active_exp_leases) >= self.limits.max_parallel_experiments:
                if not wait_if_full:
                    return (
                        None,
                        "queued",
                        f"Parallel experiment pool full ({len(self._active_exp_leases)}/{self.limits.max_parallel_experiments}).",
                    )
                was_queued = True
                self._queued_exp_waiters.append(owner_id)
                if on_queued is not None:
                    on_queued(self.snapshot_state(run_id))
                try:
                    while len(self._active_exp_leases) >= self.limits.max_parallel_experiments:
                        await self._exp_cond.wait()
                finally:
                    if owner_id in self._queued_exp_waiters:
                        self._queued_exp_waiters.remove(owner_id)

            lease = ResourceLease(
                lease_id=f"lease_exp_{uuid.uuid4().hex[:10]}",
                run_id=run_id,
                kind="experiment",
                owner_id=owner_id,
                is_exploration=False,
            )
            self._active_exp_leases[lease.lease_id] = lease
            return lease, ("queued" if was_queued else "granted"), "Parallel experiment slot leased."

    async def release_experiment(self, lease_id: str) -> None:
        async with self._exp_cond:
            self._active_exp_leases.pop(lease_id, None)
            self._exp_cond.notify_all()

    @asynccontextmanager
    async def experiment_slot(
        self,
        *,
        run_id: str,
        owner_id: str,
        on_queued: Any | None = None,
    ) -> AsyncIterator[tuple[ResourceLease, AcquireOutcome]]:
        lease, outcome, reason = await self.try_acquire_experiment(
            run_id=run_id,
            owner_id=owner_id,
            wait_if_full=True,
            on_queued=on_queued,
        )
        if lease is None:
            raise ResourceDeniedError(reason)
        try:
            yield lease, outcome
        finally:
            await self.release_experiment(lease.lease_id)
