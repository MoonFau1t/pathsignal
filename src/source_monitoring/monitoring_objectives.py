from typing import Iterable

from src.source_monitoring.models import (
    MonitoringObjectiveCode,
    MonitoringObjectiveDefinition,
)


MONITORING_OBJECTIVES: tuple[MonitoringObjectiveDefinition, ...] = (
    MonitoringObjectiveDefinition(
        code=MonitoringObjectiveCode.OPPORTUNITY,
        label="Opportunity",
        description=(
            "Direct or emerging career opportunities, including jobs, "
            "internships, graduate programs, fellowships, portfolio-company "
            "hiring, and credible team-expansion signals."
        ),
        supported_signal_examples=(
            "entry-level hiring",
            "internship and fellowship openings",
            "graduate program availability",
            "portfolio-company hiring signals",
            "team expansion that may create roles",
        ),
    ),
    MonitoringObjectiveDefinition(
        code=MonitoringObjectiveCode.ORGANIZATION,
        label="Organization",
        description=(
            "Developments within a company or institution, including new "
            "teams, products, business lines, funding, investments, "
            "partnerships, market entry, organizational change, and expansion."
        ),
        supported_signal_examples=(
            "new business lines",
            "new teams or functions",
            "funding and investment activity",
            "partnerships and market entry",
            "organizational expansion",
        ),
    ),
    MonitoringObjectiveDefinition(
        code=MonitoringObjectiveCode.INDUSTRY,
        label="Industry",
        description=(
            "Changes in markets, industries, technologies, regulation, "
            "competition, investment activity, customer demand, and adoption "
            "patterns."
        ),
        supported_signal_examples=(
            "market demand shifts",
            "technology adoption patterns",
            "regulatory changes",
            "competitive movement",
            "investment and funding trends",
        ),
    ),
    MonitoringObjectiveDefinition(
        code=MonitoringObjectiveCode.CAREER_PATH,
        label="Career Path",
        description=(
            "Intelligence about a professional path itself, including "
            "responsibilities, hiring requirements, candidate backgrounds, "
            "skills, seniority patterns, team structures, entry routes, and "
            "transition opportunities."
        ),
        supported_signal_examples=(
            "role responsibilities",
            "hiring requirements",
            "candidate background patterns",
            "entry routes and transitions",
            "team structures and seniority patterns",
        ),
    ),
)


def get_monitoring_objectives() -> tuple[MonitoringObjectiveDefinition, ...]:
    """
    Return the fixed V1 objective taxonomy.
    """

    return MONITORING_OBJECTIVES


def monitoring_objective_codes() -> tuple[str, ...]:
    return tuple(objective.code.value for objective in MONITORING_OBJECTIVES)


def validate_monitoring_objectives(
    objectives: Iterable[MonitoringObjectiveDefinition],
) -> tuple[MonitoringObjectiveDefinition, ...]:
    """
    Ensure callers did not alter the fixed top-level taxonomy.
    """

    objective_tuple = tuple(objectives)
    codes = tuple(objective.code.value for objective in objective_tuple)

    if codes != monitoring_objective_codes():
        raise ValueError(
            "MonitoringObjective taxonomy must contain exactly: "
            f"{monitoring_objective_codes()}."
        )

    return objective_tuple
