from __future__ import annotations

from ..schemas import Persona

SPECIALIST_ROLES: tuple[str, ...] = ("paper", "data", "analysis", "validation", "support")

DEFAULT_PERSONA_SPECS: list[dict[str, object]] = [
    {"persona_id": "persona_main", "role": "main", "label": "Main", "is_main": True},
    {"persona_id": "persona_paper", "role": "paper", "label": "Paper", "is_main": False},
    {"persona_id": "persona_data", "role": "data", "label": "Data", "is_main": False},
    {"persona_id": "persona_analysis", "role": "analysis", "label": "Analysis", "is_main": False},
    {"persona_id": "persona_validation", "role": "validation", "label": "Validation", "is_main": False},
    {"persona_id": "persona_support", "role": "support", "label": "Support", "is_main": False},
]


def build_initial_personas() -> list[Persona]:
    """
    Build the configured visual roster (L1.5 §2).
    All 6 canonical role classes start `idle` with `runtime_agent_id=None` and `task_ids=[]`;
    visual presence NEVER implies an active backend LLM subprocess.
    """
    return [
        Persona(
            persona_id=str(spec["persona_id"]),
            role=str(spec["role"]),
            label=str(spec["label"]),
            runtime_agent_id=None,
            state="idle",
            is_main=bool(spec["is_main"]),
            task_ids=[],
        )
        for spec in DEFAULT_PERSONA_SPECS
    ]


def build_dynamic_persona(
    *,
    role: str,
    instance_index: int,
    label: str | None = None,
    runtime_agent_id: str | None = None,
) -> Persona:
    """
    Construct an additional specialist instance inside one of the 5 specialist role classes
    when Main Agent dynamically scales out parallel workers (`persona_{role}_{index}`).
    Main Boss remains strictly singleton (`is_main=False` for dynamic workers).
    """
    clean_role = role.strip().lower()
    if clean_role not in SPECIALIST_ROLES:
        raise ValueError(f"Dynamic spawning only supports specialist roles {SPECIALIST_ROLES}, got '{role}'.")
    if instance_index < 2:
        raise ValueError(f"Dynamic specialist instance_index must be >= 2, got {instance_index}.")
    persona_id = f"persona_{clean_role}_{instance_index}"
    clean_label = (label or f"{clean_role.capitalize()} #{instance_index}").strip()
    return Persona(
        persona_id=persona_id,
        role=clean_role,
        label=clean_label,
        runtime_agent_id=runtime_agent_id,
        state="assigned",
        is_main=False,
        task_ids=[],
    )
