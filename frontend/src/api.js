export async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

export function loadWorkspace() {
  return Promise.all([
    api("/api/claims"),
    api("/api/config"),
    api("/api/actions"),
    api("/api/events?limit=500"),
    api("/api/usage?limit=1000"),
    api("/api/warranties"),
    api("/api/data-summary"),
  ]).then(([claims, config, actions, events, usage, warranties, dataSummary]) => ({
    claims,
    config,
    actions,
    events,
    usage,
    warranties,
    dataSummary,
  }));
}
