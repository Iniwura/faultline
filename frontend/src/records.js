function parseList(value) {
  if (Array.isArray(value)) return value;
  if (typeof value === "string") {
    const parsed = JSON.parse(value);
    if (Array.isArray(parsed)) return parsed;
  }
  throw new Error("Contract owner index did not return an array.");
}

export function normalizeSource(record) {
  return {
    ...record,
    id: record.source_id,
    title: record.tracked_claim?.slice(0, 52) || record.source_id,
    claim: record.tracked_claim || "",
    friendly: record.state || "UNKNOWN"
  };
}

export function normalizeDecision(record) {
  const displayState = record.effective_state || record.state || "UNKNOWN";
  return {
    ...record,
    id: record.decision_id,
    title: record.question?.slice(0, 52) || record.decision_id,
    persisted_state: record.state,
    state: displayState,
    friendly: displayState
  };
}

export async function refreshOwnerRecords({ read, owner }) {
  if (!owner) {
    return { sources: [], decisions: [] };
  }

  const normalizedOwner = owner.toLowerCase();

  const [sourceIdsValue, decisionIdsValue] = await Promise.all([
    read("get_owner_source_ids", [normalizedOwner]),
    read("get_owner_decision_ids", [normalizedOwner])
  ]);

  const sourceIds = parseList(sourceIdsValue);
  const decisionIds = parseList(decisionIdsValue);

  const [sourceRecords, decisionRecords] = await Promise.all([
    Promise.all(sourceIds.map((id) => read("get_source", [id]))),
    Promise.all(decisionIds.map((id) => read("get_decision", [id])))
  ]);

  return {
    sources: sourceRecords.map(normalizeSource),
    decisions: decisionRecords.map(normalizeDecision)
  };
}
