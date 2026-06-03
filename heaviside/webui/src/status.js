export function statusSeverity(s) {
  return {
    exact: 'success', recommended: 'info', partial: 'warn',
    no_substitute: 'danger', keep_original: 'secondary', not_fitted: 'secondary',
  }[s] || 'secondary'
}

export function jobSeverity(s) {
  return {
    done: 'success', error: 'danger', cancelled: 'secondary',
    running: 'info', queued: 'warn',
  }[s] || 'secondary'
}
