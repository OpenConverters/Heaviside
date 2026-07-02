// Cross-reference verdict severities. The closed set mirrors the backend
// SubstitutionStatus enum: exact / recommended / partial / no_substitute.
// ('exact' also covers parts kept as-is because they're already the target.)
export function statusSeverity(s) {
  return {
    exact: 'success', recommended: 'info', partial: 'warn',
    no_substitute: 'danger',
  }[s] || 'secondary'
}

export function jobSeverity(s) {
  return {
    done: 'success', error: 'danger', cancelled: 'secondary',
    running: 'info', queued: 'warn',
  }[s] || 'secondary'
}
