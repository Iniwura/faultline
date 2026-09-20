# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import genlayer as gl
from genlayer.types import *


SCHEMA_VERSION = "faultline.v1"

SOURCE_CURRENT = "SOURCE_CURRENT"
SOURCE_CHANGED = "SOURCE_CHANGED"
SOURCE_UNRESOLVED = "SOURCE_UNRESOLVED"

DECISION_VALID = "DECISION_VALID"
DECISION_STALE = "DECISION_STALE"
DECISION_INVALIDATED = "DECISION_INVALIDATED"
DECISION_UNRESOLVED = "DECISION_UNRESOLVED"

MATERIAL_CHANGE = "MATERIAL_CHANGE"
NO_MATERIAL_CHANGE = "NO_MATERIAL_CHANGE"
UNRESOLVED = "UNRESOLVED"

STILL_VALID = "STILL_VALID"
INVALIDATED = "INVALIDATED"

MAX_ID = 96
MAX_URL = 2048
MAX_CLAIM = 4000
MAX_QUESTION = 4000
MAX_REASON = 512
MAX_OBSERVED_CONTENT = 32768
MAX_DEPENDENCIES = 8
MAX_FANOUT = 32
MAX_PROPAGATION_NODES = 128
MAX_PROPAGATION_DEPTH = 32

SOURCE_RESULT_KEYS = {"change", "reason"}
DECISION_RESULT_KEYS = {"outcome", "reason", "affected_dependency_ids"}


@gl.storage.allow
@dataclass
class SourceRecord:
    source_id: str
    owner: str
    url: str
    tracked_claim: str
    revision: u256
    last_content_hash: str
    last_semantic_result: str
    state: str
    fingerprint: str
    revision_fingerprint: str


@gl.storage.allow
@dataclass
class DecisionRecord:
    decision_id: str
    owner: str
    question: str
    dependencies_json: str
    dependency_revision_snapshot: str
    revision: u256
    result: str
    state: str
    fingerprint: str
    revision_fingerprint: str


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(label: str, *parts: Any) -> str:
    payload = _canonical([label, *parts])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_text(value: Any, maximum: int, field: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise gl.vm.UserError(field + " is empty or too long.")
    return value


def _validate_id(value: Any, field: str) -> str:
    value = _validate_text(value, MAX_ID, field)
    for character in value:
        if not (character.isalnum() or character in "._-"):
            raise gl.vm.UserError(field + " contains an invalid character.")
    return value


def _validate_url(value: Any) -> str:
    value = _validate_text(value, MAX_URL, "url")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.fragment:
        raise gl.vm.UserError("url must be an HTTPS URL without a fragment.")
    return value


def _validate_source_result(result: Any) -> dict[str, str]:
    if not isinstance(result, dict) or set(result.keys()) != SOURCE_RESULT_KEYS:
        raise gl.vm.UserError("Source semantic result must have exactly two keys.")
    change = result.get("change")
    reason = result.get("reason")
    if type(change) is not str or change not in (
        MATERIAL_CHANGE,
        NO_MATERIAL_CHANGE,
        UNRESOLVED,
    ):
        raise gl.vm.UserError("Source semantic result has an invalid change value.")
    if type(reason) is not str or not reason.strip() or len(reason) > MAX_REASON:
        raise gl.vm.UserError("Source semantic result reason is invalid.")
    return {"change": change, "reason": reason}


def _validate_observation(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or set(result.keys()) != {"content_hash", "semantic"}:
        raise gl.vm.UserError("Source observation result shape is invalid.")
    content_hash = result.get("content_hash")
    if type(content_hash) is not str or len(content_hash) != 64:
        raise gl.vm.UserError("Source content hash is invalid.")
    semantic = _validate_source_result(result.get("semantic"))
    return {"content_hash": content_hash, "semantic": semantic}


def _validate_decision_result(
    result: Any, dependencies: list[str]
) -> dict[str, Any]:
    if not isinstance(result, dict) or set(result.keys()) != DECISION_RESULT_KEYS:
        raise gl.vm.UserError("Decision result must have exactly three keys.")
    outcome = result.get("outcome")
    reason = result.get("reason")
    affected = result.get("affected_dependency_ids")
    if type(outcome) is not str or outcome not in (
        STILL_VALID,
        INVALIDATED,
        UNRESOLVED,
    ):
        raise gl.vm.UserError("Decision result has an invalid outcome.")
    if type(reason) is not str or not reason.strip() or len(reason) > MAX_REASON:
        raise gl.vm.UserError("Decision result reason is invalid.")
    if type(affected) is not list or len(affected) > MAX_DEPENDENCIES:
        raise gl.vm.UserError("Decision affected_dependency_ids is invalid.")
    seen: list[str] = []
    for dependency_id in affected:
        if type(dependency_id) is not str or dependency_id not in dependencies:
            raise gl.vm.UserError("Decision result names an unknown affected dependency.")
        if dependency_id in seen:
            raise gl.vm.UserError("Decision result repeats an affected dependency.")
        seen.append(dependency_id)
    return {
        "outcome": outcome,
        "reason": reason,
        "affected_dependency_ids": sorted(seen),
    }


def _source_prompt(
    url: str, tracked_claim: str, previous_hash: str, previous_result: str, body: str
) -> str:
    previous = previous_result if previous_result else "NONE"
    return (
        "You are evaluating one registered real-world source for FAULTLINE.\n"
        "Decide whether the observed content materially changes the tracked claim.\n"
        "A material change is a change that could alter a downstream decision relying "
        "on the claim. Do not infer facts that are not present in the content.\n\n"
        "Registered URL: "
        + url
        + "\nTracked claim: "
        + tracked_claim
        + "\nPrevious observed content SHA-256: "
        + previous_hash
        + "\nPrevious semantic result JSON: "
        + previous
        + "\nCurrent rendered content:\n"
        + body
        + "\n\nRespond with exactly one JSON object with exactly these keys and no others: "
        '{"change":"MATERIAL_CHANGE|NO_MATERIAL_CHANGE|UNRESOLVED","reason":"..."}'
        "The reason must be concise, factual, and no longer than 512 characters. "
        "Use UNRESOLVED when the content is missing, contradictory, or insufficient."
    )


def _decision_prompt(
    question: str,
    dependencies_json: str,
    snapshot_json: str,
    context_json: str,
) -> str:
    return (
        "You are rechecking a FAULTLINE decision against its current dependency graph.\n"
        "Use the question, current dependency state, current revision, current semantic "
        "or result record, and the stored dependency revision snapshot.\n\n"
        "Question: "
        + question
        + "\nDependency IDs (canonical JSON): "
        + dependencies_json
        + "\nStored dependency revision snapshot: "
        + snapshot_json
        + "\nCurrent dependency context: "
        + context_json
        + "\n\nRespond with exactly one JSON object with exactly these keys and no others: "
        '{"outcome":"STILL_VALID|INVALIDATED|UNRESOLVED",'
        '"reason":"...","affected_dependency_ids":["..."]}'
        "\nEvery affected_dependency_id must be one of the listed dependency IDs. "
        "The reason must be concise and no longer than 512 characters."
    )


class Faultline(gl.contract.Contract):
    """Bounded dependency freshness and invalidation graph."""

    sources: gl.storage.TreeMap[str, SourceRecord]
    decisions: gl.storage.TreeMap[str, DecisionRecord]
    node_types: gl.storage.TreeMap[str, str]
    dependents: gl.storage.TreeMap[str, str]

    def __init__(self):
        pass

    @gl.public.write
    def register_source(self, source_id: str, url: str, tracked_claim: str) -> str:
        source_id = _validate_id(source_id, "source_id")
        url = _validate_url(url)
        tracked_claim = _validate_text(tracked_claim, MAX_CLAIM, "tracked_claim")
        if self.node_types.get(source_id, ""):
            raise gl.vm.UserError("Node id already exists.")

        owner = str(gl.message.sender_address).lower()
        fingerprint = _hash(
            "FAULTLINE-SOURCE-DEFINITION-V1",
            source_id,
            owner,
            url,
            tracked_claim,
        )
        initial_result = ""
        revision_fingerprint = _hash(
            "FAULTLINE-SOURCE-REVISION-V1", source_id, 0, "", initial_result
        )
        self.sources[source_id] = SourceRecord(
            source_id=source_id,
            owner=owner,
            url=url,
            tracked_claim=tracked_claim,
            revision=0,
            last_content_hash="",
            last_semantic_result=initial_result,
            state=SOURCE_CURRENT,
            fingerprint=fingerprint,
            revision_fingerprint=revision_fingerprint,
        )
        self.node_types[source_id] = "SOURCE"
        self.dependents[source_id] = "[]"
        return json.dumps(
            {
                "source_id": source_id,
                "state": SOURCE_CURRENT,
                "revision": 0,
                "fingerprint": fingerprint,
            },
            sort_keys=True,
        )

    @gl.public.write
    def create_decision(
        self, decision_id: str, question: str, dependencies: list[str]
    ) -> str:
        decision_id = _validate_id(decision_id, "decision_id")
        question = _validate_text(question, MAX_QUESTION, "question")
        if type(dependencies) is not list or not dependencies:
            raise gl.vm.UserError("A decision must have at least one dependency.")
        if len(dependencies) > MAX_DEPENDENCIES:
            raise gl.vm.UserError("Decision dependency bound exceeded.")
        if self.node_types.get(decision_id, ""):
            raise gl.vm.UserError("Node id already exists.")

        canonical_dependencies: list[str] = []
        for dependency_id in dependencies:
            dependency_id = _validate_id(dependency_id, "dependency_id")
            if dependency_id == decision_id:
                raise gl.vm.UserError("A decision cannot depend on itself.")
            if dependency_id in canonical_dependencies:
                raise gl.vm.UserError("Duplicate decision dependency.")
            if not self.node_types.get(dependency_id, ""):
                raise gl.vm.UserError("Unknown decision dependency.")
            canonical_dependencies.append(dependency_id)
        canonical_dependencies.sort()
        self._assert_no_cycle(decision_id, canonical_dependencies)

        snapshot: dict[str, int] = {}
        for dependency_id in canonical_dependencies:
            snapshot[dependency_id] = self._node_revision(dependency_id)
            current_dependents = self._dependents_for(dependency_id)
            if len(current_dependents) >= MAX_FANOUT:
                raise gl.vm.UserError("Dependency fanout bound exceeded.")

        owner = str(gl.message.sender_address).lower()
        dependencies_json = _canonical(canonical_dependencies)
        snapshot_json = _canonical(snapshot)
        fingerprint = _hash(
            "FAULTLINE-DECISION-DEFINITION-V1",
            decision_id,
            owner,
            question,
            canonical_dependencies,
        )
        result = ""
        revision_fingerprint = _hash(
            "FAULTLINE-DECISION-REVISION-V1", decision_id, 0, result, snapshot
        )
        self.decisions[decision_id] = DecisionRecord(
            decision_id=decision_id,
            owner=owner,
            question=question,
            dependencies_json=dependencies_json,
            dependency_revision_snapshot=snapshot_json,
            revision=0,
            result=result,
            state=DECISION_STALE,
            fingerprint=fingerprint,
            revision_fingerprint=revision_fingerprint,
        )
        self.node_types[decision_id] = "DECISION"
        self.dependents[decision_id] = "[]"
        for dependency_id in canonical_dependencies:
            self._add_dependent(dependency_id, decision_id)

        return json.dumps(
            {
                "decision_id": decision_id,
                "state": DECISION_STALE,
                "revision": 0,
                "dependencies": canonical_dependencies,
                "fingerprint": fingerprint,
            },
            sort_keys=True,
        )

    @gl.public.write
    def check_source(self, source_id: str) -> str:
        source = self._source(source_id)
        previous_hash = source.last_content_hash
        previous_result = source.last_semantic_result
        try:
            observation = self._consensus_source_observation(
                source.url,
                source.tracked_claim,
                previous_hash,
                previous_result,
            )
        except Exception:
            self._mark_source_unresolved(source)
            return self._source_write_result(source)

        content_hash = observation["content_hash"]
        semantic = observation["semantic"]
        if semantic["change"] == MATERIAL_CHANGE and previous_hash == content_hash:
            self._mark_source_unresolved(source)
            return self._source_write_result(source)

        source.last_content_hash = content_hash
        source.last_semantic_result = _canonical(semantic)
        source.revision_fingerprint = _hash(
            "FAULTLINE-SOURCE-REVISION-V1",
            source.source_id,
            int(source.revision),
            content_hash,
            semantic,
        )
        if semantic["change"] == MATERIAL_CHANGE:
            source.revision = source.revision + 1
            source.state = SOURCE_CHANGED
            source.revision_fingerprint = _hash(
                "FAULTLINE-SOURCE-REVISION-V1",
                source.source_id,
                int(source.revision),
                content_hash,
                semantic,
            )
            self._propagate_stale(source.source_id)
        elif semantic["change"] == NO_MATERIAL_CHANGE:
            source.state = SOURCE_CURRENT
        else:
            source.state = SOURCE_UNRESOLVED
            self._propagate_stale(source.source_id)
        return self._source_write_result(source)

    @gl.public.write
    def recheck_decision(self, decision_id: str) -> str:
        decision = self._decision(decision_id)
        dependencies = self._dependencies_for(decision)
        self._assert_dependencies_exist(dependencies)
        snapshot = json.loads(decision.dependency_revision_snapshot)
        context = self._dependency_context(dependencies, snapshot)
        prompt = _decision_prompt(
            decision.question,
            decision.dependencies_json,
            decision.dependency_revision_snapshot,
            _canonical(context),
        )
        try:
            result = self._consensus_decision_result(prompt, dependencies)
        except Exception:
            result = {
                "outcome": UNRESOLVED,
                "reason": "Decision evaluation unavailable.",
                "affected_dependency_ids": [],
            }

        if result["outcome"] == STILL_VALID and not self._dependencies_safe(dependencies):
            raise gl.vm.UserError(
                "A decision cannot become valid while a dependency is unsafe."
            )

        result_json = _canonical(result)
        if result["outcome"] == STILL_VALID:
            decision.revision = decision.revision + 1
            decision.state = DECISION_VALID
            decision.dependency_revision_snapshot = self._current_snapshot_json(
                dependencies
            )
            decision.result = result_json
            decision.revision_fingerprint = _hash(
                "FAULTLINE-DECISION-REVISION-V1",
                decision.decision_id,
                int(decision.revision),
                result,
                json.loads(decision.dependency_revision_snapshot),
            )
        elif result["outcome"] == INVALIDATED:
            decision.revision = decision.revision + 1
            decision.state = DECISION_INVALIDATED
            decision.dependency_revision_snapshot = self._current_snapshot_json(
                dependencies
            )
            decision.result = result_json
            decision.revision_fingerprint = _hash(
                "FAULTLINE-DECISION-REVISION-V1",
                decision.decision_id,
                int(decision.revision),
                result,
                json.loads(decision.dependency_revision_snapshot),
            )
            self._propagate_stale(decision.decision_id)
        else:
            decision.state = DECISION_UNRESOLVED
            decision.result = result_json
            decision.revision_fingerprint = _hash(
                "FAULTLINE-DECISION-REVISION-V1",
                decision.decision_id,
                int(decision.revision),
                result,
                json.loads(decision.dependency_revision_snapshot),
            )
            self._propagate_stale(decision.decision_id)

        return self._decision_write_result(decision)

    @gl.public.view
    def get_source(self, source_id: str) -> str:
        source = self._source(source_id)
        return json.dumps(
            {
                "source_id": source.source_id,
                "owner": source.owner,
                "url": source.url,
                "tracked_claim": source.tracked_claim,
                "revision": int(source.revision),
                "last_content_hash": source.last_content_hash,
                "last_semantic_result": json.loads(source.last_semantic_result)
                if source.last_semantic_result
                else None,
                "state": source.state,
                "fingerprint": source.fingerprint,
                "revision_fingerprint": source.revision_fingerprint,
            },
            sort_keys=True,
        )

    @gl.public.view
    def get_decision(self, decision_id: str) -> str:
        decision = self._decision(decision_id)
        return json.dumps(
            {
                "decision_id": decision.decision_id,
                "owner": decision.owner,
                "question": decision.question,
                "dependencies": self._dependencies_for(decision),
                "dependency_revision_snapshot": json.loads(
                    decision.dependency_revision_snapshot
                ),
                "revision": int(decision.revision),
                "result": json.loads(decision.result) if decision.result else None,
                "state": decision.state,
                "effective_state": self._effective_state(
                    decision.decision_id, [], 0
                ),
                "fingerprint": decision.fingerprint,
                "revision_fingerprint": decision.revision_fingerprint,
            },
            sort_keys=True,
        )

    @gl.public.view
    def get_dependencies(self, decision_id: str) -> str:
        return _canonical(self._dependencies_for(self._decision(decision_id)))

    @gl.public.view
    def get_dependents(self, node_id: str) -> str:
        self._require_node(node_id)
        return _canonical(self._dependents_for(node_id))

    @gl.public.view
    def get_effective_decision_state(self, decision_id: str) -> str:
        self._decision(decision_id)
        return self._effective_state(decision_id, [], 0)

    def _source(self, source_id: str) -> SourceRecord:
        source = self.sources.get(source_id, None)
        if source is None:
            raise gl.vm.UserError("Source does not exist.")
        return source

    def _decision(self, decision_id: str) -> DecisionRecord:
        decision = self.decisions.get(decision_id, None)
        if decision is None:
            raise gl.vm.UserError("Decision does not exist.")
        return decision

    def _require_node(self, node_id: str) -> str:
        node_type = self.node_types.get(node_id, "")
        if not node_type:
            raise gl.vm.UserError("Node does not exist.")
        return node_type

    def _dependencies_for(self, decision: DecisionRecord) -> list[str]:
        values = json.loads(decision.dependencies_json)
        if type(values) is not list:
            raise gl.vm.UserError("Stored decision dependencies are invalid.")
        return values

    def _dependents_for(self, node_id: str) -> list[str]:
        values = json.loads(self.dependents.get(node_id, "[]"))
        if type(values) is not list:
            raise gl.vm.UserError("Stored reverse dependency list is invalid.")
        return values

    def _add_dependent(self, node_id: str, decision_id: str) -> None:
        values = self._dependents_for(node_id)
        if decision_id in values:
            raise gl.vm.UserError("Duplicate reverse dependency edge.")
        if len(values) >= MAX_FANOUT:
            raise gl.vm.UserError("Dependency fanout bound exceeded.")
        values.append(decision_id)
        values.sort()
        self.dependents[node_id] = _canonical(values)

    def _assert_dependencies_exist(self, dependencies: list[str]) -> None:
        for dependency_id in dependencies:
            self._require_node(dependency_id)

    def _assert_no_cycle(self, decision_id: str, dependencies: list[str]) -> None:
        visited: list[str] = []
        pending = list(dependencies)
        traversed = 0
        while pending:
            node_id = pending.pop()
            if node_id == decision_id:
                raise gl.vm.UserError("Decision dependency cycle rejected.")
            if node_id in visited:
                continue
            visited.append(node_id)
            traversed += 1
            if traversed > MAX_PROPAGATION_NODES:
                raise gl.vm.UserError("Cycle-check bound exceeded.")
            if self.node_types.get(node_id, "") == "DECISION":
                pending.extend(self._dependencies_for(self._decision(node_id)))

    def _node_revision(self, node_id: str) -> int:
        node_type = self._require_node(node_id)
        if node_type == "SOURCE":
            return int(self._source(node_id).revision)
        return int(self._decision(node_id).revision)

    def _current_snapshot_json(self, dependencies: list[str]) -> str:
        snapshot: dict[str, int] = {}
        for dependency_id in dependencies:
            snapshot[dependency_id] = self._node_revision(dependency_id)
        return _canonical(snapshot)

    def _dependency_context(
        self, dependencies: list[str], snapshot: dict[str, Any]
    ) -> list[dict[str, Any]]:
        context: list[dict[str, Any]] = []
        for dependency_id in dependencies:
            node_type = self._require_node(dependency_id)
            if node_type == "SOURCE":
                source = self._source(dependency_id)
                context.append(
                    {
                        "id": dependency_id,
                        "type": "SOURCE",
                        "state": source.state,
                        "revision": int(source.revision),
                        "stored_snapshot_revision": snapshot.get(dependency_id),
                        "content_hash": source.last_content_hash,
                        "semantic_result": json.loads(source.last_semantic_result)
                        if source.last_semantic_result
                        else None,
                    }
                )
            else:
                decision = self._decision(dependency_id)
                context.append(
                    {
                        "id": dependency_id,
                        "type": "DECISION",
                        "persisted_state": decision.state,
                        "effective_state": self._effective_state(
                            dependency_id, [], 0
                        ),
                        "revision": int(decision.revision),
                        "stored_snapshot_revision": snapshot.get(dependency_id),
                        "result": json.loads(decision.result)
                        if decision.result
                        else None,
                    }
                )
        return context

    def _dependencies_safe(self, dependencies: list[str]) -> bool:
        for dependency_id in dependencies:
            node_type = self._require_node(dependency_id)
            if node_type == "SOURCE":
                if self._source(dependency_id).state != SOURCE_CURRENT:
                    return False
            elif self._effective_state(dependency_id, [], 0) != DECISION_VALID:
                return False
        return True

    def _effective_state(
        self, decision_id: str, visited: list[str], depth: int
    ) -> str:
        if depth > MAX_PROPAGATION_DEPTH or decision_id in visited:
            return DECISION_STALE
        decision = self._decision(decision_id)
        if decision.state != DECISION_VALID:
            return decision.state
        snapshot = json.loads(decision.dependency_revision_snapshot)
        next_visited = visited + [decision_id]
        for dependency_id in self._dependencies_for(decision):
            if self._node_revision(dependency_id) != int(snapshot.get(dependency_id, -1)):
                return DECISION_STALE
            node_type = self._require_node(dependency_id)
            if node_type == "SOURCE":
                source_state = self._source(dependency_id).state
                if source_state == SOURCE_UNRESOLVED:
                    return DECISION_UNRESOLVED
                if source_state != SOURCE_CURRENT:
                    return DECISION_STALE
            else:
                dependency_state = self._effective_state(
                    dependency_id, next_visited, depth + 1
                )
                if dependency_state == DECISION_UNRESOLVED:
                    return DECISION_UNRESOLVED
                if dependency_state != DECISION_VALID:
                    return DECISION_STALE
        return DECISION_VALID

    def _consensus_source_observation(
        self, url: str, tracked_claim: str, previous_hash: str, previous_result: str
    ) -> dict[str, Any]:
        def observe() -> dict[str, Any]:
            body = gl.nondet.web.render(url, mode="text")
            if type(body) is not str or not body.strip() or len(body) > MAX_OBSERVED_CONTENT:
                raise gl.vm.UserError("Rendered source content is unavailable.")
            raw = gl.nondet.exec_prompt(
                _source_prompt(url, tracked_claim, previous_hash, previous_result, body),
                response_format="json",
            )
            semantic = _validate_source_result(raw)
            return {"content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(), "semantic": semantic}

        def validator_fn(leader_result: Any) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False
            try:
                leader_observation = _validate_observation(leader_result.calldata)
                validator_observation = _validate_observation(observe())
            except Exception:
                return False
            return (
                leader_observation["semantic"]["change"]
                == validator_observation["semantic"]["change"]
            )

        return _validate_observation(gl.vm.run_nondet(observe, validator_fn))

    def _consensus_decision_result(
        self, prompt: str, dependencies: list[str]
    ) -> dict[str, Any]:
        def evaluate() -> dict[str, Any]:
            return _validate_decision_result(
                gl.nondet.exec_prompt(prompt, response_format="json"), dependencies
            )

        def validator_fn(leader_result: Any) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False
            try:
                leader_data = _validate_decision_result(
                    leader_result.calldata, dependencies
                )
                validator_data = _validate_decision_result(evaluate(), dependencies)
            except Exception:
                return False
            return (
                leader_data["outcome"] == validator_data["outcome"]
                and leader_data["affected_dependency_ids"]
                == validator_data["affected_dependency_ids"]
            )

        return _validate_decision_result(
            gl.vm.run_nondet(evaluate, validator_fn), dependencies
        )

    def _mark_source_unresolved(self, source: SourceRecord) -> None:
        semantic = {"change": UNRESOLVED, "reason": "External evidence unavailable."}
        source.state = SOURCE_UNRESOLVED
        source.last_semantic_result = _canonical(semantic)
        source.revision_fingerprint = _hash(
            "FAULTLINE-SOURCE-REVISION-V1",
            source.source_id,
            int(source.revision),
            source.last_content_hash,
            semantic,
        )
        self._propagate_stale(source.source_id)

    def _source_write_result(self, source: SourceRecord) -> str:
        return json.dumps(
            {
                "source_id": source.source_id,
                "state": source.state,
                "revision": int(source.revision),
                "last_content_hash": source.last_content_hash,
                "semantic_result": json.loads(source.last_semantic_result)
                if source.last_semantic_result
                else None,
                "revision_fingerprint": source.revision_fingerprint,
            },
            sort_keys=True,
        )

    def _decision_write_result(self, decision: DecisionRecord) -> str:
        return json.dumps(
            {
                "decision_id": decision.decision_id,
                "state": decision.state,
                "effective_state": self._effective_state(
                    decision.decision_id, [], 0
                ),
                "revision": int(decision.revision),
                "result": json.loads(decision.result) if decision.result else None,
                "dependency_revision_snapshot": json.loads(
                    decision.dependency_revision_snapshot
                ),
                "revision_fingerprint": decision.revision_fingerprint,
            },
            sort_keys=True,
        )

    def _propagate_stale(self, node_id: str) -> None:
        pending: list[tuple[str, int]] = [(node_id, 0)]
        visited: list[str] = []
        traversed = 0
        while pending:
            current, depth = pending.pop(0)
            if current in visited:
                continue
            visited.append(current)
            traversed += 1
            if traversed > MAX_PROPAGATION_NODES:
                raise gl.vm.UserError("Staleness propagation bound exceeded.")
            if depth > MAX_PROPAGATION_DEPTH:
                raise gl.vm.UserError("Staleness propagation depth exceeded.")
            for dependent_id in self._dependents_for(current):
                dependent = self._decision(dependent_id)
                dependent.state = DECISION_STALE
                pending.append((dependent_id, depth + 1))
