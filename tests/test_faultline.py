import hashlib
import json
from pathlib import Path

import pytest


CONTRACT_PATH = Path(__file__).resolve().parents[1] / "contracts" / "faultline.py"
SOURCE_URL = "https://vendor.example.com/certification"
CURRENT_BODY = "Vendor X maintains ISO 27001 certification. Certificate is active."
CHANGED_BODY = "Vendor X certification expired and is no longer active."


def source_result(change: str, reason: str = "The observed claim is clear.") -> str:
    return json.dumps({"change": change, "reason": reason})


def decision_result(
    outcome: str,
    reason: str = "The dependency facts still support the conclusion.",
    affected: list[str] | None = None,
    extra: dict | None = None,
) -> str:
    result = {
        "outcome": outcome,
        "reason": reason,
        "affected_dependency_ids": affected or [],
    }
    if extra:
        result.update(extra)
    return json.dumps(result)


def deploy(direct_deploy):
    return direct_deploy(CONTRACT_PATH)


def mock_source(direct_vm, body: str, semantic: str) -> None:
    direct_vm.clear_mocks()
    direct_vm.mock_web(r"https://vendor[.]example[.]com/certification", {"body": body})
    direct_vm.mock_llm(r".*", semantic)


def register(contract, direct_vm, sender, source_id="source-a") -> None:
    direct_vm.sender = sender
    contract.register_source(
        source_id,
        SOURCE_URL,
        "Vendor X maintains ISO 27001 certification.",
    )


def create_b(contract, direct_vm, sender, decision_id="decision-b", dependencies=None):
    direct_vm.sender = sender
    return contract.create_decision(
        decision_id,
        "Vendor X is approved for procurement.",
        dependencies or ["source-a"],
    )


def read_source(contract, source_id="source-a"):
    return json.loads(contract.get_source(source_id))


def read_decision(contract, decision_id="decision-b"):
    return json.loads(contract.get_decision(decision_id))


def observe_current(
    contract,
    direct_vm,
    sender,
    source_id="source-a",
    url_pattern=r"https://vendor[.]example[.]com/certification",
    body=CURRENT_BODY,
):
    direct_vm.clear_mocks()
    direct_vm.mock_web(url_pattern, {"body": body})
    direct_vm.mock_llm(r".*", source_result("NO_MATERIAL_CHANGE"))
    direct_vm.sender = sender
    return json.loads(contract.check_source(source_id))


def test_register_source_and_duplicate_rejected(direct_vm, direct_deploy, direct_alice):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    source = read_source(contract)
    assert source["state"] == "SOURCE_UNOBSERVED"
    assert source["last_content_hash"] == ""
    assert source["last_semantic_result"] is None
    assert source["revision"] == 0
    assert source["fingerprint"] == hashlib.sha256(
        json.dumps(
            [
                "FAULTLINE-SOURCE-DEFINITION-V1",
                "source-a",
                "0x" + direct_alice.hex(),
                SOURCE_URL,
                "Vendor X maintains ISO 27001 certification.",
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    with direct_vm.expect_revert("Node id already exists"):
        register(contract, direct_vm, direct_alice)


def test_create_decision_and_unknown_self_duplicate_dependencies_rejected(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    create_b(contract, direct_vm, direct_alice)
    with direct_vm.expect_revert("Unknown decision dependency"):
        create_b(contract, direct_vm, direct_alice, "unknown", ["missing"])
    with direct_vm.expect_revert("cannot depend on itself"):
        create_b(contract, direct_vm, direct_alice, "self", ["self"])
    with direct_vm.expect_revert("Duplicate decision dependency"):
        create_b(contract, direct_vm, direct_alice, "duplicate", ["source-a", "source-a"])


def test_new_decision_starts_stale_and_reverse_edges_are_deterministic(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    created = json.loads(create_b(contract, direct_vm, direct_alice))
    assert created["state"] == "DECISION_STALE"
    assert read_decision(contract)["effective_state"] == "DECISION_STALE"
    assert json.loads(contract.get_dependencies("decision-b")) == ["source-a"]
    assert json.loads(contract.get_dependents("source-a")) == ["decision-b"]


def test_cycle_guard_rejects_future_mutation_path(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    create_b(contract, direct_vm, direct_alice)
    direct_vm.sender = direct_alice
    contract.create_decision(
        "decision-c",
        "The contract may proceed.",
        ["decision-b"],
    )
    with direct_vm.expect_revert("cycle rejected"):
        contract._assert_no_cycle("decision-b", ["decision-c"])


def test_first_recheck_establishes_valid_state_and_full_consensus_result(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    create_b(contract, direct_vm, direct_alice)
    observe_current(contract, direct_vm, direct_alice)
    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", decision_result("STILL_VALID"))
    direct_vm.sender = direct_alice
    result = json.loads(contract.recheck_decision("decision-b"))
    assert result["state"] == "DECISION_VALID"
    assert result["effective_state"] == "DECISION_VALID"
    assert result["revision"] == 1
    assert set(result["result"]) == {
        "outcome",
        "reason",
        "affected_dependency_ids",
    }
    direct_vm.clear_mocks()
    direct_vm.mock_llm(
        r".*", decision_result("STILL_VALID", "A different valid explanation.")
    )
    assert direct_vm.run_validator() is True
    tampered = dict(result["result"])
    tampered["reason"] = "tampered"
    tampered["outcome"] = "INVALIDATED"
    assert direct_vm.run_validator(leader_result=tampered) is False


def test_source_reason_difference_does_not_disagree(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    mock_source(direct_vm, CURRENT_BODY, source_result("NO_MATERIAL_CHANGE", "Leader explanation."))
    direct_vm.sender = direct_alice
    contract.check_source("source-a")
    mock_source(direct_vm, CURRENT_BODY, source_result("NO_MATERIAL_CHANGE", "Validator explanation."))
    assert direct_vm.run_validator() is True


def test_source_enum_disagreement_is_rejected(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    mock_source(direct_vm, CURRENT_BODY, source_result("NO_MATERIAL_CHANGE"))
    direct_vm.sender = direct_alice
    contract.check_source("source-a")
    mock_source(direct_vm, CURRENT_BODY, source_result("MATERIAL_CHANGE"))
    assert direct_vm.run_validator() is False


def test_decision_outcome_disagreement_is_rejected(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    create_b(contract, direct_vm, direct_alice)
    observe_current(contract, direct_vm, direct_alice)
    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", decision_result("STILL_VALID"))
    direct_vm.sender = direct_alice
    contract.recheck_decision("decision-b")
    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", decision_result("INVALIDATED", affected=["source-a"]))
    assert direct_vm.run_validator() is False


def test_decision_affected_dependency_mismatch_is_rejected(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    direct_vm.sender = direct_alice
    contract.register_source(
        "source-z", "https://other.example.com/fact", "Other fact remains true."
    )
    contract.create_decision(
        "decision-b",
        "Vendor X is approved for procurement.",
        ["source-a", "source-z"],
    )
    direct_vm.mock_llm(
        r".*", decision_result("INVALIDATED", affected=["source-a", "source-z"])
    )
    contract.recheck_decision("decision-b")
    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", decision_result("INVALIDATED", affected=["source-a"]))
    assert direct_vm.run_validator() is False


def test_malformed_leader_result_is_rejected_by_custom_validator(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    create_b(contract, direct_vm, direct_alice)
    direct_vm.mock_llm(r".*", decision_result("STILL_VALID"))
    direct_vm.sender = direct_alice
    contract.recheck_decision("decision-b")
    assert direct_vm.run_validator(
        leader_result={"outcome": "NOT_VALID", "reason": "bad", "affected_dependency_ids": []}
    ) is False


def test_extra_leader_keys_are_rejected_by_custom_validator(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    create_b(contract, direct_vm, direct_alice)
    direct_vm.mock_llm(r".*", decision_result("STILL_VALID"))
    direct_vm.sender = direct_alice
    contract.recheck_decision("decision-b")
    assert direct_vm.run_validator(
        leader_result={
            "outcome": "STILL_VALID",
            "reason": "ok",
            "affected_dependency_ids": [],
            "extra": True,
        }
    ) is False


def test_unordered_affected_dependency_ids_normalize_consistently(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    direct_vm.sender = direct_alice
    contract.register_source(
        "source-z", "https://other.example.com/fact", "Other fact remains true."
    )
    contract.create_decision(
        "decision-b",
        "Vendor X is approved for procurement.",
        ["source-a", "source-z"],
    )
    direct_vm.mock_llm(
        r".*", decision_result("INVALIDATED", affected=["source-z", "source-a"])
    )
    result = json.loads(contract.recheck_decision("decision-b"))
    assert result["result"]["affected_dependency_ids"] == ["source-a", "source-z"]
    direct_vm.clear_mocks()
    direct_vm.mock_llm(
        r".*", decision_result("INVALIDATED", affected=["source-a", "source-z"])
    )
    assert direct_vm.run_validator() is True


def test_no_material_change_does_not_increment_source_revision(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    mock_source(direct_vm, CURRENT_BODY, source_result("NO_MATERIAL_CHANGE"))
    direct_vm.sender = direct_alice
    assert json.loads(contract.check_source("source-a"))["revision"] == 0
    mock_source(direct_vm, CURRENT_BODY, source_result("NO_MATERIAL_CHANGE"))
    assert json.loads(contract.check_source("source-a"))["revision"] == 0
    assert read_source(contract)["state"] == "SOURCE_CURRENT"


def test_material_change_increments_revision_and_marks_direct_dependent_stale(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    observe_current(contract, direct_vm, direct_alice)
    create_b(contract, direct_vm, direct_alice)
    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", decision_result("STILL_VALID"))
    direct_vm.sender = direct_alice
    contract.recheck_decision("decision-b")
    mock_source(direct_vm, CHANGED_BODY, source_result("MATERIAL_CHANGE", "Certification expired."))
    result = json.loads(contract.check_source("source-a"))
    assert result["state"] == "SOURCE_CHANGED"
    assert result["revision"] == 1
    assert read_decision(contract)["state"] == "DECISION_STALE"


def test_material_change_cascades_to_grandchild_but_not_unrelated_valid_decision(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice, "source-a")
    direct_vm.sender = direct_alice
    contract.register_source("source-z", "https://other.example.com/fact", "Other fact remains true.")
    observe_current(contract, direct_vm, direct_alice, "source-a")
    observe_current(
        contract,
        direct_vm,
        direct_alice,
        "source-z",
        r"https://other[.]example[.]com/fact",
        "Other fact remains true.",
    )
    create_b(contract, direct_vm, direct_alice)
    contract.create_decision("decision-c", "The contract may proceed.", ["decision-b"])
    contract.create_decision("decision-z", "The unrelated decision remains valid.", ["source-z"])
    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", decision_result("STILL_VALID"))
    contract.recheck_decision("decision-b")
    contract.recheck_decision("decision-c")
    contract.recheck_decision("decision-z")
    mock_source(direct_vm, CHANGED_BODY, source_result("MATERIAL_CHANGE", "Certification expired."))
    direct_vm.sender = direct_alice
    contract.check_source("source-a")
    assert read_decision(contract, "decision-b")["state"] == "DECISION_STALE"
    assert read_decision(contract, "decision-c")["state"] == "DECISION_STALE"
    assert read_decision(contract, "decision-z")["state"] == "DECISION_VALID"


def test_invalidated_decision_propagates_stale_to_descendants(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    observe_current(contract, direct_vm, direct_alice)
    create_b(contract, direct_vm, direct_alice)
    direct_vm.sender = direct_alice
    contract.create_decision("decision-c", "The contract may proceed.", ["decision-b"])
    direct_vm.mock_llm(r".*", decision_result("STILL_VALID"))
    contract.recheck_decision("decision-b")
    contract.recheck_decision("decision-c")
    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", decision_result("INVALIDATED", "Procurement approval is no longer supported.", ["source-a"]))
    result = json.loads(contract.recheck_decision("decision-b"))
    assert result["state"] == "DECISION_INVALIDATED"
    assert read_decision(contract, "decision-c")["state"] == "DECISION_STALE"


def test_recheck_refreshes_dependency_snapshot_after_source_revision(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    observe_current(contract, direct_vm, direct_alice)
    create_b(contract, direct_vm, direct_alice)
    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", decision_result("STILL_VALID"))
    direct_vm.sender = direct_alice
    contract.recheck_decision("decision-b")
    mock_source(direct_vm, CHANGED_BODY, source_result("MATERIAL_CHANGE", "Certification expired."))
    contract.check_source("source-a")
    mock_source(direct_vm, CHANGED_BODY, source_result("NO_MATERIAL_CHANGE", "The changed fact is stable."))
    contract.check_source("source-a")
    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", decision_result("STILL_VALID"))
    result = json.loads(contract.recheck_decision("decision-b"))
    assert result["state"] == "DECISION_VALID"
    assert result["dependency_revision_snapshot"] == {"source-a": 1}


def test_effective_state_detects_revision_mismatch_without_llm(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    observe_current(contract, direct_vm, direct_alice)
    create_b(contract, direct_vm, direct_alice)
    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", decision_result("STILL_VALID"))
    direct_vm.sender = direct_alice
    contract.recheck_decision("decision-b")
    source = contract.sources["source-a"]
    source.revision = source.revision + 1
    assert contract.get_effective_decision_state("decision-b") == "DECISION_STALE"


@pytest.mark.parametrize(
    "response",
    [
        json.dumps({"change": "NO_MATERIAL_CHANGE", "reason": "ok", "extra": True}),
        json.dumps({"change": "NOT_A_CHANGE", "reason": "ok"}),
        json.dumps({"change": "NO_MATERIAL_CHANGE", "reason": 7}),
    ],
)
def test_malformed_source_semantic_response_fails_closed(
    response, direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    mock_source(direct_vm, CURRENT_BODY, response)
    direct_vm.sender = direct_alice
    result = json.loads(contract.check_source("source-a"))
    assert result["state"] == "SOURCE_UNRESOLVED"
    assert result["revision"] == 0


@pytest.mark.parametrize(
    "response",
    [
        decision_result("STILL_VALID", extra={"extra": True}),
        decision_result("STILL_VALID", affected=["missing"]),
        decision_result("STILL_VALID", affected=["source-a", "source-a"]),
    ],
)
def test_malformed_decision_response_fails_closed(
    response, direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    create_b(contract, direct_vm, direct_alice)
    direct_vm.mock_llm(r".*", response)
    direct_vm.sender = direct_alice
    result = json.loads(contract.recheck_decision("decision-b"))
    assert result["state"] == "DECISION_UNRESOLVED"
    assert result["revision"] == 0


def test_network_or_model_failure_is_unresolved_and_never_success(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    direct_vm.sender = direct_alice
    source_result_value = json.loads(contract.check_source("source-a"))
    assert source_result_value["state"] == "SOURCE_UNRESOLVED"
    assert source_result_value["revision"] == 0


def test_deep_graph_is_rejected_at_admission_before_propagation_can_fail(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    observe_current(contract, direct_vm, direct_alice)
    direct_vm.sender = direct_alice

    previous = "source-a"
    for index in range(32):
        decision_id = "chain-" + str(index)
        created = json.loads(
            contract.create_decision(
                decision_id,
                "A bounded chain decision.",
                [previous],
            )
        )
        assert created["depth"] == index + 1
        previous = decision_id

    with direct_vm.expect_revert("depth bound exceeded"):
        contract.create_decision(
            "chain-32",
            "This node would exceed the safe propagation depth.",
            ["chain-31"],
        )

    assert json.loads(contract.get_dependents("chain-31")) == []

    mock_source(
        direct_vm,
        CHANGED_BODY,
        source_result("MATERIAL_CHANGE", "Changed."),
    )
    direct_vm.sender = direct_alice
    result = json.loads(contract.check_source("source-a"))
    assert result["state"] == "SOURCE_CHANGED"
    assert result["revision"] == 1


def test_broad_graph_material_change_propagates_without_revert(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    observe_current(contract, direct_vm, direct_alice)
    direct_vm.sender = direct_alice

    for index in range(32):
        contract.create_decision(
            "wide-" + str(index),
            "A broad graph decision.",
            ["source-a"],
        )

    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", decision_result("STILL_VALID"))
    for index in range(32):
        contract.recheck_decision("wide-" + str(index))

    with direct_vm.expect_revert("fanout bound exceeded"):
        contract.create_decision(
            "wide-overflow",
            "This edge would exceed the fanout bound.",
            ["source-a"],
        )

    mock_source(
        direct_vm,
        CHANGED_BODY,
        source_result("MATERIAL_CHANGE", "Changed."),
    )
    direct_vm.sender = direct_alice
    result = json.loads(contract.check_source("source-a"))
    assert result["state"] == "SOURCE_CHANGED"

    for index in range(32):
        assert read_decision(contract, "wide-" + str(index))["state"] == "DECISION_STALE"


def test_fingerprints_are_deterministic_and_bind_revision_result(
    direct_vm, direct_deploy, direct_alice
):
    first = deploy(direct_deploy)
    register(first, direct_vm, direct_alice)
    first_fp = read_source(first)["fingerprint"]
    assert first_fp == read_source(first)["fingerprint"]

    mock_source(direct_vm, CURRENT_BODY, source_result("NO_MATERIAL_CHANGE"))
    direct_vm.sender = direct_alice
    before = read_source(first)["revision_fingerprint"]
    first.check_source("source-a")
    after = read_source(first)["revision_fingerprint"]
    assert before != after


def test_documented_three_node_demo_flow(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    direct_vm.sender = direct_alice
    contract.create_decision("decision-b", "Vendor X is approved for procurement.", ["source-a"])
    contract.create_decision("decision-c", "The contract with Vendor X may proceed.", ["decision-b"])
    observe_current(contract, direct_vm, direct_alice)
    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", decision_result("STILL_VALID"))
    contract.recheck_decision("decision-b")
    contract.recheck_decision("decision-c")
    assert read_source(contract)["state"] == "SOURCE_CURRENT"
    assert read_decision(contract, "decision-b")["effective_state"] == "DECISION_VALID"
    assert read_decision(contract, "decision-c")["effective_state"] == "DECISION_VALID"

    mock_source(direct_vm, CHANGED_BODY, source_result("MATERIAL_CHANGE", "Certification expired."))
    contract.check_source("source-a")
    assert read_decision(contract, "decision-b")["effective_state"] == "DECISION_STALE"
    assert read_decision(contract, "decision-c")["effective_state"] == "DECISION_STALE"

    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", decision_result("INVALIDATED", "The procurement approval is no longer supported.", ["source-a"]))
    contract.recheck_decision("decision-b")
    assert read_decision(contract, "decision-b")["state"] == "DECISION_INVALIDATED"
    assert read_decision(contract, "decision-c")["effective_state"] == "DECISION_STALE"


def test_creator_indexes_expose_created_records(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    owner = "0x" + direct_alice.hex()

    register(contract, direct_vm, direct_alice, "owned-source")
    direct_vm.sender = direct_alice
    contract.create_decision(
        "owned-decision",
        "A creator-owned record can be rediscovered.",
        ["owned-source"],
    )

    assert json.loads(contract.get_owner_source_ids(owner.upper())) == [
        "owned-source"
    ]
    assert json.loads(contract.get_owner_decision_ids(owner.upper())) == [
        "owned-decision"
    ]

    source = json.loads(contract.get_source("owned-source"))
    decision = json.loads(contract.get_decision("owned-decision"))

    assert source["source_id"] == "owned-source"
    assert source["owner"] == owner
    assert decision["decision_id"] == "owned-decision"
    assert decision["owner"] == owner


def test_unobserved_source_cannot_support_valid_decision(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    register(contract, direct_vm, direct_alice)
    create_b(contract, direct_vm, direct_alice)

    direct_vm.mock_llm(r".*", decision_result("STILL_VALID"))
    direct_vm.sender = direct_alice

    result = json.loads(contract.recheck_decision("decision-b"))

    assert read_source(contract)["state"] == "SOURCE_UNOBSERVED"
    assert result["state"] == "DECISION_UNRESOLVED"
    assert result["effective_state"] == "DECISION_UNRESOLVED"
    assert result["revision"] == 0


def test_graph_node_bound_is_enforced_at_admission(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy(direct_deploy)
    direct_vm.sender = direct_alice

    for index in range(128):
        contract.register_source(
            f"bounded-source-{index}",
            SOURCE_URL,
            "Vendor X maintains ISO 27001 certification.",
        )

    assert int(contract.node_count) == 128

    with direct_vm.expect_revert("Graph node bound exceeded"):
        contract.register_source(
            "bounded-source-overflow",
            SOURCE_URL,
            "Vendor X maintains ISO 27001 certification.",
        )

    assert int(contract.node_count) == 128
