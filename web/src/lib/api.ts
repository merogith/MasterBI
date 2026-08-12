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

export interface SurveyOption {
  value: string;
  label: string;
  note?: string;
  disabled?: boolean;
  approximate?: boolean;
}

export interface SurveyQuestion {
  id: string;
  text: string;
  help: string;
  group: string;
  options: SurveyOption[];
  default?: string;
  optional?: boolean;
  unlocks?: string;
}

export interface Survey {
  questions: SurveyQuestion[];
  groups: string[];
  optional_group: string;
  count: number;
  core_count: number;
  optional_count: number;
}

export interface UploadColumn {
  name: string;
  semantic?: string;
  dtype?: string;
  non_null?: number;
  null_pct?: number;
  unique?: number;
  samples?: (string | number)[];
  problems?: string[];
}

export interface UploadShape {
  shape: string;
  target_table: string;
  usable: boolean;
  matches?: { field: string; column: string | null; status?: string; reason?: string }[];
  missing_required?: string[];
}

export interface UploadProfile {
  filename: string;
  /** Where the server put the file. Adoption records this, not the original
   *  name — two uploads called `data.csv` are two different files. */
  stored_as?: string;
  table_key?: string;
  read?: { rows: number; columns: string[]; notes?: string[] };
  profile?: { rows?: number; columns?: UploadColumn[]; problems?: string[] };
  shapes?: UploadShape[];
}

/** Multipart, so no JSON content-type — `api()` would set one and break the
 *  boundary. Profiling is read-only: nothing about a run changes here. */
export async function profileUpload(file: File): Promise<UploadProfile> {
  const body = new FormData();
  body.append('file', file);
  const response = await fetch('/api/ingest/profile', { method: 'POST', body });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* a non-JSON error body is still an error */
    }
    throw new ApiError(detail, response.status);
  }
  return await response.json();
}

export const getSurvey = () => api<Survey>('/api/survey');
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

export interface RerunReport {
  run_id: string;
  status: RunStatus;
  dirty: string[];
  reused: string[];
  estimated_seconds?: number;
}

export const rerunRun = (runId: string) =>
  api<RerunReport>(`/api/runs/${runId}/rerun`, { method: 'POST' });

// -- studio ----------------------------------------------------------------

export interface CatalogOptions {
  artifacts: string[];
  detectors: string[];
  themes: string[];
  sections: { id: string; title: string }[];
  exhibits: string[];
  widths: string[];
  fact_tables: string[];
  ops: { name: string; label: string; help: string; params: Record<string, unknown> }[];
}

export interface PlanReport {
  dirty: string[];
  reused: string[];
  estimated_seconds: number;
}

export interface CatalogKpi {
  id: string;
  name: string;
  unit: string;
  timing?: string;
  origin?: string;
}

export interface AiStatus {
  available: boolean;
  reason?: string;
  default_model?: string;
  narratable_sections?: string[];
}

/** The spec is deliberately untyped here. It is a large nested Pydantic model
 *  whose shape belongs to the server, and mirroring all of it in TypeScript
 *  would create a second definition to keep in sync — the exact drift this
 *  repo tests for elsewhere. The Studio reads and writes it by path. */
export type Spec = Record<string, any>;

export const getSpec = (runId: string) => api<Spec>(`/api/runs/${runId}/spec`);
export const getPlan = (runId: string) => api<PlanReport>(`/api/runs/${runId}/plan`);
export const getOptions = () => api<CatalogOptions>('/api/catalog/options');
export const getAiStatus = () => api<AiStatus>('/api/ai/status');

export const listCatalogKpis = () =>
  api<{ kpis: CatalogKpi[] }>('/api/catalog/kpis');

export const putSpec = (runId: string, spec: Spec) =>
  api<PlanReport>(`/api/runs/${runId}/spec`, {
    method: 'PUT', body: JSON.stringify(spec),
  });

export interface Adjustment {
  token: string;
  original: string;
  applied: string;
  reason: string;
  detail: string;
}

export interface PalettePreview {
  series: string[];
  tokens: Record<string, string>;
  adjustments: Adjustment[];
  notes?: string[];
  against_surface: number;
  heading_ratio: number;
}

export interface DesignPreview {
  palettes: { light: PalettePreview; dark: PalettePreview };
  logo: { path: string | null; ok: boolean; data_uri?: string; error?: string };
  thresholds: { text: number; graphical: number; separation: number };
}

export const previewDesign = (body: {
  primary: string | null; accent: string | null; logo_path: string | null;
}) => api<DesignPreview>('/api/design/preview', {
  method: 'POST', body: JSON.stringify(body),
});

export interface AiEstimate {
  worst_case_tokens: number;
  worst_case_cost_usd: number;
}

export interface PlanChange {
  path: string;
  value: unknown;
  reason?: string;
  current?: unknown;
}

export interface AiPlan {
  changes: PlanChange[];
  summary?: string;
  notes?: string[];
}

export const aiEstimate = (runId: string) =>
  api<AiEstimate>(`/api/ai/estimate/${runId}`, { method: 'POST' });

export const aiPlan = (runId: string) =>
  api<AiPlan>(`/api/ai/plan/${runId}`, { method: 'POST' });

export const aiApply = (runId: string, changes: { path: string; value: unknown }[]) =>
  api<PlanReport>(`/api/ai/apply/${runId}`, {
    method: 'POST', body: JSON.stringify({ changes }),
  });

export interface FormulaCheck {
  ok: boolean;
  error?: { message: string };
  unknown?: string[];
  value?: number | null;
}

export const validateFormula = (body: {
  expression: string; scope: string; run_id?: string; table?: string;
}) => api<FormulaCheck>('/api/formula/validate', {
  method: 'POST', body: JSON.stringify(body),
});

export const listOps = () =>
  api<{ ops: { name: string; label: string; help: string;
               params: Record<string, unknown> }[] }>('/api/ops');

export const listTables = (runId: string) =>
  api<{ name: string; rows: number }[]>(`/api/runs/${runId}/tables`);
