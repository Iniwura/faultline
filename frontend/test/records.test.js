import test from "node:test";
import assert from "node:assert/strict";

import { refreshOwnerRecords } from "../src/records.js";

test("refresh reads creator indexes and canonical contract records", async () => {
  const calls = [];

  const responses = new Map([
    ["get_owner_source_ids", ["source-user"]],
    ["get_owner_decision_ids", ["decision-user"]],
    ["get_source:source-user", {
      source_id: "source-user",
      owner: "0xabc",
      tracked_claim: "The canonical tracked claim",
      url: "https://example.com/source",
      state: "SOURCE_UNOBSERVED",
      revision: 0
    }],
    ["get_decision:decision-user", {
      decision_id: "decision-user",
      owner: "0xabc",
      question: "Should the action proceed?",
      dependencies: ["source-user"],
      state: "DECISION_STALE",
      effective_state: "DECISION_STALE",
      revision: 0
    }]
  ]);

  async function read(method, args = []) {
    calls.push([method, args]);

    if (method === "get_source" || method === "get_decision") {
      return responses.get(`${method}:${args[0]}`);
    }

    return responses.get(method);
  }

  const result = await refreshOwnerRecords({
    read,
    owner: "0xAbC"
  });

  assert.deepEqual(calls.slice(0, 2), [
    ["get_owner_source_ids", ["0xabc"]],
    ["get_owner_decision_ids", ["0xabc"]]
  ]);

  assert.equal(result.sources.length, 1);
  assert.equal(result.sources[0].id, "source-user");
  assert.equal(result.sources[0].state, "SOURCE_UNOBSERVED");
  assert.equal(result.sources[0].claim, "The canonical tracked claim");

  assert.equal(result.decisions.length, 1);
  assert.equal(result.decisions[0].id, "decision-user");
  assert.deepEqual(result.decisions[0].dependencies, ["source-user"]);
});

test("refresh replaces stale local assumptions with updated chain state", async () => {
  let decisionState = "DECISION_STALE";

  async function read(method, args = []) {
    if (method === "get_owner_source_ids") return [];
    if (method === "get_owner_decision_ids") return ["decision-user"];

    if (method === "get_decision" && args[0] === "decision-user") {
      return {
        decision_id: "decision-user",
        question: "Should the action proceed?",
        dependencies: [],
        state: decisionState,
        effective_state: decisionState,
        revision: decisionState === "DECISION_VALID" ? 1 : 0
      };
    }

    throw new Error(`Unexpected read: ${method}`);
  }

  const first = await refreshOwnerRecords({ read, owner: "0xabc" });
  assert.equal(first.decisions[0].state, "DECISION_STALE");
  assert.equal(first.decisions[0].revision, 0);

  decisionState = "DECISION_VALID";

  const refreshed = await refreshOwnerRecords({ read, owner: "0xabc" });
  assert.equal(refreshed.decisions[0].state, "DECISION_VALID");
  assert.equal(refreshed.decisions[0].revision, 1);
});

test("no wallet returns empty user records without contract reads", async () => {
  let called = false;

  const result = await refreshOwnerRecords({
    owner: "",
    read: async () => {
      called = true;
    }
  });

  assert.deepEqual(result, { sources: [], decisions: [] });
  assert.equal(called, false);
});
