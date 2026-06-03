// Thin fetch wrapper around the Heaviside FastAPI server.

async function jget(url) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${url} → ${r.status}`)
  return r.json()
}
async function jpost(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${url} → ${r.status}`)
  return r.json()
}

export const api = {
  topologies: () => jget('/topologies'),
  manufacturers: () => jget('/manufacturers'),
  catalog: (cat, q, limit = 50) =>
    jget(`/catalog/${cat}?limit=${limit}&q=${encodeURIComponent(q || '')}`),
  jobs: () => jget('/jobs'),
  job: (id) => jget(`/jobs/${id}`),
  cancelJob: (id) => fetch(`/jobs/${id}/cancel`, { method: 'POST' }),
  deleteJob: (id) => fetch(`/jobs/${id}`, { method: 'DELETE' }),
  submitDesign: (body) => jpost('/jobs/design', body),
  submitCrossref: (body) => jpost('/jobs/crossref', body),
  submitCrossrefUrl: (body) => jpost('/jobs/crossref/from-url', body),
  submitCrossrefPdf: (file, target) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(
      `/jobs/crossref/from-pdf?target_manufacturer=${encodeURIComponent(target)}`,
      { method: 'POST', body: fd },
    ).then((r) => {
      if (!r.ok) throw new Error(`upload → ${r.status}`)
      return r.json()
    })
  },
}

// Poll a job until terminal; calls onTick(job) each poll. Returns the result.
export async function pollJob(id, onTick) {
  for (;;) {
    const j = await api.job(id)
    onTick?.(j)
    if (j.status === 'done') return j.result
    if (j.status === 'error') throw new Error(j.error || 'job failed')
    if (j.status === 'cancelled') throw new Error('job cancelled')
    await new Promise((res) => setTimeout(res, 2500))
  }
}
