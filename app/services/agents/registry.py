"""Agent registry — the declared roster of 27 logical LLM jobs (Milestone 4).

The registry is DATA (it never calls an LLM). It declares, per agent: topic,
task type, dependencies, routing spec (evidence selectors), owned parameters,
prompt reference, response-schema reference and model tier. From this it derives:

* an ``agent_definition_hash`` per agent over its SEMANTIC definition (owned
  parameters, evidence selectors identity, dependencies, task type, response
  schema) so the cache invalidates when semantics change even if the prompt text
  does not (Remediation 7 / Req 28);
* an ``agent_registry_version`` + ``agent_registry_hash`` recorded on the
  analysis run and final snapshot (Req 19.5);
* a dependency DAG supporting topological waves (M5) and dependent-descendant
  traversal for targeted reruns (Remediation 2 / Req 29).

Load-time validation rejects duplicate ids, unknown/cyclic dependencies and any
parameter owned by more than one agent (Req 5.2).

The roster is EXACTLY 27 logical jobs (Req 5.3):
    15 narrow / early extraction  (6 Business + 6 Financial + 3 Structuring)
  +  4 risk-to-mitigant
  +  4 orchestrators (Business, Financial, Structuring, Credit)
  +  4 challengers  (Business, Financial, Structuring, Cross-Topic)
  = 27
Peak initially-ready Wave 0 jobs are ~15 (the narrow + early-extraction set).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.hashing import content_hash
from app.schemas.agentic import ModelTier, Topic
from app.services.agents.router import RoutingSpec, Selector

AGENT_REGISTRY_VERSION = "agent-registry-1.0.0"


class AgentRegistryError(ValueError):
    """Raised when the declared agent roster is invalid."""


@dataclass(frozen=True)
class AgentDefinition:
    """One declared agent (Req 5.1)."""

    agent_id: str
    topic: Topic
    task_type: str  # extract|interpret|classify|orchestrate|challenge|mitigant
    model_tier: ModelTier
    dependencies: tuple[str, ...] = ()
    owned_parameters: tuple[str, ...] = ()
    prompt_name: str | None = None
    response_schema_ref: str | None = None
    # Routing selectors describing which evidence the agent may receive. The
    # selector *names* (not the callables) feed the definition hash so a change
    # in what an agent is allowed to see invalidates its cache.
    routing: RoutingSpec | None = None
    # Identity labels for selectors, used in the definition hash (callables are
    # not hashable deterministically). Keep these in sync with ``routing``.
    selector_identity: tuple[str, ...] = ()

    @property
    def prompt(self) -> str:
        return self.prompt_name or self.agent_id

    def definition_hash(self) -> str:
        """Deterministic hash over the agent's SEMANTIC definition (Req 28)."""
        return content_hash(
            {
                "agent_id": self.agent_id,
                "topic": self.topic.value,
                "task_type": self.task_type,
                "model_tier": self.model_tier.value,
                "dependencies": sorted(self.dependencies),
                "owned_parameters": sorted(self.owned_parameters),
                "prompt_name": self.prompt,
                "response_schema_ref": self.response_schema_ref,
                "selector_identity": sorted(self.selector_identity),
            }
        )


class AgentRegistry:
    """Validated roster of agents with DAG + descendant traversal."""

    def __init__(
        self,
        definitions: list[AgentDefinition],
        *,
        version: str = AGENT_REGISTRY_VERSION,
    ) -> None:
        self.version = version
        self._by_id: dict[str, AgentDefinition] = {}
        self._validate(definitions)
        for d in definitions:
            self._by_id[d.agent_id] = d
        # Precompute forward dependents (reverse of dependencies) for traversal.
        self._dependents: dict[str, set[str]] = {d.agent_id: set() for d in definitions}
        for d in definitions:
            for dep in d.dependencies:
                self._dependents[dep].add(d.agent_id)

    # -- validation -----------------------------------------------------------

    def _validate(self, definitions: list[AgentDefinition]) -> None:
        ids = [d.agent_id for d in definitions]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise AgentRegistryError(f"Duplicate agent_id(s): {sorted(dupes)}.")
        id_set = set(ids)
        for d in definitions:
            for dep in d.dependencies:
                if dep not in id_set:
                    raise AgentRegistryError(
                        f"Agent {d.agent_id!r} depends on unknown agent {dep!r}."
                    )
        # Parameter ownership is unique (Req 5.2).
        owner: dict[str, str] = {}
        for d in definitions:
            for p in d.owned_parameters:
                if p in owner:
                    raise AgentRegistryError(
                        f"Parameter {p!r} owned by both {owner[p]!r} and "
                        f"{d.agent_id!r}; ownership must be unique."
                    )
                owner[p] = d.agent_id
        self._check_acyclic(definitions)

    @staticmethod
    def _check_acyclic(definitions: list[AgentDefinition]) -> None:
        deps = {d.agent_id: set(d.dependencies) for d in definitions}
        WHITE, GREY, BLACK = 0, 1, 2
        color = {i: WHITE for i in deps}

        def visit(node: str, stack: tuple[str, ...]) -> None:
            color[node] = GREY
            for nxt in sorted(deps[node]):
                if color[nxt] == GREY:
                    cycle = " -> ".join([*stack, node, nxt])
                    raise AgentRegistryError(f"Cyclic agent dependency: {cycle}.")
                if color[nxt] == WHITE:
                    visit(nxt, (*stack, node))
            color[node] = BLACK

        for node in sorted(deps):
            if color[node] == WHITE:
                visit(node, ())

    # -- lookup ---------------------------------------------------------------

    def get(self, agent_id: str) -> AgentDefinition:
        try:
            return self._by_id[agent_id]
        except KeyError as exc:
            raise AgentRegistryError(f"Unknown agent {agent_id!r}.") from exc

    def all(self) -> list[AgentDefinition]:
        return list(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, agent_id: object) -> bool:
        return agent_id in self._by_id

    def owner_of(self, parameter_id: str) -> str | None:
        for d in self._by_id.values():
            if parameter_id in d.owned_parameters:
                return d.agent_id
        return None

    def definition_hash(self, agent_id: str) -> str:
        return self.get(agent_id).definition_hash()

    def registry_hash(self) -> str:
        """Hash over every agent's definition hash — the registry identity."""
        return content_hash(
            {d.agent_id: d.definition_hash() for d in self._by_id.values()}
        )

    # -- DAG --------------------------------------------------------------------

    def topological_waves(self) -> list[list[str]]:
        """Return agent ids grouped into dependency waves (Kahn's algorithm)."""
        indeg = {d.agent_id: len(d.dependencies) for d in self._by_id.values()}
        waves: list[list[str]] = []
        remaining = dict(indeg)
        while remaining:
            ready = sorted(a for a, n in remaining.items() if n == 0)
            if not ready:  # pragma: no cover - acyclicity already enforced
                raise AgentRegistryError("Dependency cycle detected during scheduling.")
            waves.append(ready)
            for a in ready:
                del remaining[a]
                for dependent in self._dependents[a]:
                    if dependent in remaining:
                        remaining[dependent] -= 1
        return waves

    def dependents_of(self, agent_id: str) -> set[str]:
        """Direct dependents (agents that declare ``agent_id`` as a dependency)."""
        return set(self._dependents.get(agent_id, set()))

    def descendants_of(self, agent_ids: set[str] | str) -> set[str]:
        """All transitive dependents of the given node(s) (Remediation 2 / Req 29).

        This is the set a targeted rerun must invalidate and recompute. It never
        includes the starting node(s) themselves — only their descendants.
        """
        if isinstance(agent_ids, str):
            agent_ids = {agent_ids}
        seen: set[str] = set()
        stack = list(agent_ids)
        while stack:
            current = stack.pop()
            for dependent in self._dependents.get(current, set()):
                if dependent not in seen:
                    seen.add(dependent)
                    stack.append(dependent)
        return seen


# ---------------------------------------------------------------------------
# Selector helpers for the default roster
# ---------------------------------------------------------------------------


def _topic_in(*topics: str) -> Selector:
    allowed = set(topics)
    return lambda item: bool(set(item.get("candidate_topics", [])) & allowed) or (
        item.get("topic") in allowed
    )


def _name_in(*names: str) -> Selector:
    allowed = set(names)
    return lambda item: item.get("name") in allowed or item.get("parameter_id") in allowed


def _always() -> Selector:
    return lambda item: True


# ---------------------------------------------------------------------------
# The default 27-job roster
# ---------------------------------------------------------------------------

# Business narrow (6)
_BUSINESS_NARROW = [
    ("business_model", "business_model",
     ("segment_share", "segment_hhi", "revenue_model_stability")),
    ("competition_pricing", "competition_pricing",
     ("competitive_position", "pricing_power")),
    ("customer_supplier_contract", "customer_supplier_contract",
     ("customer_concentration", "supplier_dependence", "contract_termination_risk")),
    ("management_governance", "management_governance",
     ("management_quality", "key_person_dependency", "governance_concern")),
    ("ma_capex_execution", "ma_capex_execution",
     ("acquisition_strategy", "integration_risk", "capex_strategy")),
    ("regulatory_material_events", "regulatory_material_events",
     ("regulatory_dependency", "litigation_risk", "material_event")),
]

# Financial narrow (6)
_FINANCIAL_NARROW = [
    ("ebitda_adjustments", "ebitda_adjustments",
     ("ebitda_addback_quality", "recurring_vs_exceptional")),
    ("cashflow_working_capital", "cashflow_working_capital",
     ("cash_conversion_sustainability", "working_capital_driver")),
    ("debt_liquidity_terms", "debt_liquidity_terms",
     ("accessible_revolver", "restricted_cash_caveat", "financing_dependency")),
    ("covenant_extraction", "covenant_extraction",
     ("max_net_leverage_covenant", "min_coverage_covenant", "covenant_testing")),
    ("accounting_audit_quality", "accounting_audit_quality",
     ("audit_opinion_quality", "going_concern_flag", "restatement_flag")),
    ("forecast_stress_drivers", "forecast_stress_drivers",
     ("downside_driver", "stress_sensitivity")),
]

# Early Structuring extraction (3)
_STRUCTURING_EXTRACTION = [
    ("facility_terms", "facility_terms",
     ("facility_amount_term", "tenor_term", "amortization_term")),
    ("collateral_security_guarantee", "collateral_security_guarantee",
     ("collateral_term", "guarantee_term", "security_term")),
    ("legal_undertakings_conditions", "legal_undertakings_conditions",
     ("undertaking_term", "condition_precedent", "mandatory_prepayment_term")),
]

# Risk-to-mitigant (4) — depend on accepted Business+Financial conclusions + obligor score
_MITIGANT = [
    ("liquidity_refinancing_mitigant", "mitigant", ("liquidity_refinancing_mitigant_proposal",)),
    ("leverage_coverage_mitigant", "mitigant", ("leverage_coverage_mitigant_proposal",)),
    ("business_concentration_mitigant", "mitigant", ("business_concentration_mitigant_proposal",)),
    ("governance_information_mitigant", "mitigant", ("governance_information_mitigant_proposal",)),
]


def default_registry() -> AgentRegistry:
    """Build the canonical 27-job agent registry."""
    defs: list[AgentDefinition] = []

    def narrow(agent_id, prompt, params, topic, *, narrative_topics=(), fact_names=()):
        routing = RoutingSpec(
            agent_id=agent_id,
            fact_selector=_name_in(*fact_names) if fact_names else None,
            narrative_selector=_topic_in(*narrative_topics) if narrative_topics else None,
        )
        return AgentDefinition(
            agent_id=agent_id,
            topic=topic,
            task_type=prompt,
            model_tier=ModelTier.NARROW,
            owned_parameters=tuple(params),
            prompt_name=agent_id,
            response_schema_ref=agent_id,
            routing=routing,
            selector_identity=(
                *(f"fact:{n}" for n in fact_names),
                *(f"narrative:{t}" for t in narrative_topics),
            ),
        )

    for agent_id, prompt, params in _BUSINESS_NARROW:
        defs.append(narrow(agent_id, prompt, params, Topic.BUSINESS,
                           narrative_topics=("management", "competitive_position",
                                             "industry_risk", "ownership",
                                             "corporate_structure")))
    for agent_id, prompt, params in _FINANCIAL_NARROW:
        covenant = agent_id == "covenant_extraction"
        routing = RoutingSpec(
            agent_id=agent_id,
            covenant_term_selector=_always() if covenant else None,
            narrative_selector=_topic_in("qualitative_debt_terms") if not covenant else None,
        )
        defs.append(
            AgentDefinition(
                agent_id=agent_id,
                topic=Topic.FINANCIAL,
                task_type=prompt,
                model_tier=ModelTier.NARROW,
                owned_parameters=tuple(params),
                prompt_name=agent_id,
                response_schema_ref=agent_id,
                routing=routing,
                selector_identity=("covenant_terms",) if covenant
                else ("narrative:qualitative_debt_terms",),
            )
        )
    for agent_id, prompt, params in _STRUCTURING_EXTRACTION:
        routing = RoutingSpec(
            agent_id=agent_id,
            facility_term_selector=_always() if agent_id == "facility_terms" else None,
            covenant_term_selector=_always()
            if agent_id == "legal_undertakings_conditions" else None,
        )
        defs.append(
            AgentDefinition(
                agent_id=agent_id,
                topic=Topic.STRUCTURING,
                task_type=prompt,
                model_tier=ModelTier.NARROW,
                owned_parameters=tuple(params),
                prompt_name=agent_id,
                response_schema_ref=agent_id,
                routing=routing,
                selector_identity=("facility_terms", "covenant_terms"),
            )
        )

    # Orchestrators
    defs.append(_orchestrator("business_orchestrator", Topic.BUSINESS,
                              [d[0] for d in _BUSINESS_NARROW]))
    defs.append(_orchestrator("financial_orchestrator", Topic.FINANCIAL,
                              [d[0] for d in _FINANCIAL_NARROW]))
    # Challengers (depend on their orchestrator)
    defs.append(_challenger("business_challenge", Topic.BUSINESS,
                            "business_orchestrator"))
    defs.append(_challenger("financial_challenge", Topic.FINANCIAL,
                            "financial_orchestrator"))

    # Risk-to-mitigant agents wait for accepted Business + Financial conclusions
    # (represented by the two challengers that gate conclusion acceptance).
    mitigant_deps = ("business_challenge", "financial_challenge")
    for agent_id, task_type, params in _MITIGANT:
        defs.append(
            AgentDefinition(
                agent_id=agent_id,
                topic=Topic.STRUCTURING,
                task_type=task_type,
                model_tier=ModelTier.NARROW,
                dependencies=mitigant_deps,
                owned_parameters=tuple(params),
                prompt_name=agent_id,
                response_schema_ref=agent_id,
                routing=RoutingSpec(agent_id=agent_id),
                selector_identity=(),
            )
        )

    # Structuring orchestrator depends on the early extraction + mitigant agents.
    structuring_deps = (
        *[d[0] for d in _STRUCTURING_EXTRACTION],
        *[d[0] for d in _MITIGANT],
    )
    defs.append(_orchestrator("structuring_orchestrator", Topic.STRUCTURING,
                              list(structuring_deps)))
    defs.append(_challenger("structuring_challenge", Topic.STRUCTURING,
                            "structuring_orchestrator"))

    # Final cross-topic.
    defs.append(
        AgentDefinition(
            agent_id="credit_orchestrator",
            topic=Topic.CROSS_TOPIC,
            task_type="orchestrate",
            model_tier=ModelTier.ORCHESTRATOR,
            dependencies=("business_challenge", "financial_challenge",
                          "structuring_challenge"),
            prompt_name="credit_orchestrator",
            response_schema_ref="credit_orchestrator",
            routing=RoutingSpec(agent_id="credit_orchestrator"),
        )
    )
    defs.append(_challenger("cross_topic_challenge", Topic.CROSS_TOPIC,
                            "credit_orchestrator"))

    return AgentRegistry(defs)


def _orchestrator(agent_id: str, topic: Topic, deps: list[str]) -> AgentDefinition:
    return AgentDefinition(
        agent_id=agent_id,
        topic=topic,
        task_type="orchestrate",
        model_tier=ModelTier.ORCHESTRATOR,
        dependencies=tuple(deps),
        prompt_name=agent_id,
        response_schema_ref=agent_id,
        routing=RoutingSpec(agent_id=agent_id),
    )


def _challenger(agent_id: str, topic: Topic, orchestrator: str) -> AgentDefinition:
    return AgentDefinition(
        agent_id=agent_id,
        topic=topic,
        task_type="challenge",
        model_tier=ModelTier.CHALLENGE,
        dependencies=(orchestrator,),
        prompt_name=agent_id,
        response_schema_ref=agent_id,
        routing=RoutingSpec(agent_id=agent_id),
    )
