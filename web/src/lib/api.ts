/* The typed client for the FastAPI server.
 *
 * The legacy front end reads these payloads as untyped objects, which is how
 * `mode: "restored"` and a missing `started_at` went unnoticed in the history
 * drawer for as long as they did. The shapes below are the server's, written
 * down once.
 */

/** Where downloadable artifacts live.
 *
 *  API paths stay bare: `tools/static_shim.js` replaces `window.fetch` itself,
 *  so on the hosted Pages build a request to `/api/runs` is intercepted and
 *  answered from frozen JSON without this file knowing. File URLs are the
 *  exception — an `href` on a download card is never fetched, so it has to be
 *  absolute against whatever base the shim resolved. Read per call, not once
 *  at load: the hosted build probes for a local server in the background and
 *  may rewrite this after the module has been evaluated.
 */
export const filesBase = (): string => window.KPI_FILES_BASE ?? '';

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: init?.body ? { 'content-type': 'application/json' } : undefined,
    ...init,
  });
  if (!response.ok) {
    // FastAPI puts the useful sentence in `detail`; falling back to the status
    // text turns "Unknown sample 'foo'" into "Not Found", which helps nobody.
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* a non-JSON error body is still an error */
    }
    throw new ApiError(detail, response.status);
  }
  return response.status === 204 ? (undefined as T) : await response.json();
}

// -- shapes ----------------------------------------------------------------

export interface Sample {
  id: string;
  title: string;
  tagline: string;
  story: string;
  watch_for?: string;
  tags?: string[];
  stats?: Record<string, string | number>;
}

export type RunStatus =
  | 'queued' | 'running' | 'done' | 'cancelled' | 'error' | 'missing';

/** One row of the history drawer. `cancelled_stage` is where a resume starts;
 *  `resumable` is false for a run that failed before writing a spec. */
export interface RunRow {
  run_id: string;
  status: RunStatus | null;
  company: string | null;
  mode: string | null;
  started_at: string | null;
  finished_at: string | null;
  cancelled_stage: string | null;
  resumable: boolean;
}

export interface CreateRunRequest {
  mode: 'sample' | 'survey' | 'surprise';
  sample_id?: string;
  answers?: Record<string, unknown>;
  company_name?: string;
  seed?: number;
}

export interface CreateRunResponse {
  run_id: string;
  status: RunStatus;
  company: string;
}

export interface StageEvent {
  stage: string;
  label: string;
  index: number;
  total: number;
  state: 'running' | 'done' | 'reused' | string;
  elapsed: number;
  eta_seconds: number;
}

export interface Progress {
  current: StageEvent;
  stages: StageEvent[];
  done: number;
  total: number;
  eta_seconds: number;
  elapsed: number;
}

export interface Kpi {
  kpi_id: string;
  name: string;
  current: number | null;
  prior_year: number | null;
  unit: string | null;
  direction: string | null;
  status: string | null;
  tier: number | null;
  computed: boolean;
}

export interface Finding {
  title?: string;
  statement?: string;
  severity: string;
  [key: string]: unknown;
}

export interface Artifact {
  name: string;
  label: string;
  kind: string;
  blurb: string;
  size: number;
  url: string;
}

export interface Summary {
  run_id: string;
  company: string;
  currency: string;
  period: string;
  objective: string;
  audience: string;
  stage: string;
  tiles: Kpi[];
  kpis: Kpi[];
  findings: Finding[];
  warnings: string[];
  artifacts: Artifact[];
}

/** `GET /api/runs/{id}` — the live cache while a run is in flight, the store's
 *  row once the process has forgotten it. `summary` appears only when done. */
export interface Run {
  run_id?: string;
  status: RunStatus;
  progress?: Progress | null;
  summary?: Summary;
  error?: string;
  cancelled_stage?: string | null;
  company?: string | null;
}

export const listSamples = () => api<Sample[]>('/api/samples');
export const listRuns = () => api<RunRow[]>('/api/runs');
export const getRun = (runId: string) => api<Run>(`/api/runs/${runId}`);

export const createRun = (body: CreateRunRequest) =>
  api<CreateRunResponse>('/api/runs', {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const cancelRun = (runId: string) =>
  api<{ status: string }>(`/api/runs/${runId}/cancel`, { method: 'POST' });
