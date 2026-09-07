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

/** An artifact's address, wherever this build is running.
 *
 *  The server hands out `/files/<run>/report.pdf` and serves it from the root.
 *  The frozen demo hands out `files/<run>/report.pdf` — relative, because the
 *  site lives under a repository sub-path — and the shim supplies the root to
 *  resolve it against. Joining those by concatenation produced
 *  `/MasterBIfiles/...`, so the slash is handled once, here. */
export function fileUrl(url: string): string {
  const base = filesBase();
  if (!base) return url;
  return `${base.replace(/\/$/, '')}/${url.replace(/^\//, '')}`;
}

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
  // Read off the sample's own profile by `GET /api/samples`, not written into
  // the gallery file. The home screen shows them, so a figure here that drifted
  // from the pack it promises would be the product lying on its first screen.
  currency?: string;
  country?: string;
  revenue?: number;
  headcount?: number;
  customers?: number;
  stage?: string;
  objective?: string;
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
  mode: 'sample' | 'survey' | 'surprise' | 'upload';
  sample_id?: string;
  answers?: Record<string, unknown>;
  company_name?: string;
  seed?: number;
  /** `upload` mode: the `stored_as` names the profile route handed back. */
  uploads?: string[];
  fill_gaps?: string[];
  spec?: Record<string, unknown>;
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
  // Columns of `facts.csv` that reached the browser and were dropped on the
  // floor. `basis` in particular is derived automatically by `TrackedTables`
  // from what each metric actually read, so it has always been correct and
  // never once been shown.
  basis?: string | null;
  target?: number | null;
  vs_target?: number | null;
  /** Absolute move against the same month a year ago, in the metric's own
   *  unit — not a percentage. Read by the driver drill-down. */
  yoy_change?: number | null;
  benchmark_p50?: number | null;
  benchmark_position?: string | null;
  owner?: string | null;
  perspective?: string | null;
  timing?: string | null;
  /** Why this KPI could not be computed. The answer to "why is this row
   *  empty", written by the engine and previously visible to nobody. */
  reason?: string | null;
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

/** One KPI's record sheet, in the Neely/Cambridge shape the library authors in.
 *  Optional almost everywhere because a pack may legitimately omit a benchmark
 *  or a target rule, and showing an empty row is honest where inventing a
 *  placeholder would not be. */
export interface RecordSheet {
  id: string;
  name: string;
  short_name?: string | null;
  perspective?: string | null;
  tier?: number | null;
  timing?: string | null;
  direction?: string | null;
  driver_parent?: string | null;
  formula?: string | null;
  unit?: string | null;
  frequency?: string | null;
  owner_role?: string | null;
  source_systems?: string[] | null;
  benchmark?: {
    p25?: number | null; p50?: number | null; p75?: number | null;
    source?: string | null;
  } | null;
  alert_bands?: { green?: number | null; red?: number | null } | null;
  target_rule?: string | null;
  applies_when?: string | null;
  requires_data?: string[] | null;
  pitfalls?: string | null;
  interpretation?: string | null;
  serves_objectives?: string[] | null;
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
  // Seven more fields the server has always sent and the app has never shown.
  // `dropped` is the sharpest of them: on a real SaaS run it holds **nineteen**
  // KPIs the engine considered and rejected, each with its reason. "Why isn't
  // X on my dashboard" had an answer the whole time, computed and unread.
  north_star?: Kpi | null;
  /** The value-driver tree over this run's KPIs. `driver_parent` is authored on
   *  56 of the 80 record sheets and had exactly one consumer — its own field
   *  declaration — until `kpi/drivers.py`. */
  drivers?: {
    roots: string[];
    dangling: Record<string, string>;
    nodes: Record<string, {
      name: string; parent: string | null; children: string[]; depth: number;
    }>;
  };
  /** The governed record sheet for every selected KPI, keyed by id. Every
   *  field has existed in `kpi/library/*.yaml` since the beginning and reached
   *  only the PDF appendix — which is the opposite of "one reviewed definition,
   *  surfaced everywhere the number appears". */
  sheets?: Record<string, RecordSheet>;
  severity_counts?: Record<string, number>;
  rationale?: Record<string, string>;
  dropped?: Record<string, string>;
  provenance?: Record<string, string>;
  confidence?: number | null;
  stages_ran?: string[];
  stages_reused?: string[];
  seconds?: number | null;
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
  /** What a user might type for this option — "gym", "haulage", "dtc". Twenty
   *  sectors is past the point where a list is a choice, and nobody types
   *  "Distribution or wholesale". */
  aliases?: string[];
  /** NACE and NAICS, shown once the option is chosen: the classification a
   *  sector was matched against is worth seeing, and worth not shouting. */
  classification?: string;
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
  /** `{question_id: [answers that keep this question]}`, all entries having to
   *  match. Data rather than an expression on purpose: the browser decides it
   *  as the user answers and `survey/engine.py` decides it again when the
   *  answers come back, and two parsers of a small language would be two things
   *  to keep in step. Kept identical by `test_the_two_evaluators_agree`. */
  show_if?: Record<string, string[]>;
}

const UNANSWERED: (string | undefined)[] = [undefined, '', '__unknown__'];

/** Whether a question applies to the business described so far.
 *
 *  Mirrors `survey/engine.py::is_visible`, including the rule that an
 *  unanswered condition keeps the question visible — the survey is answered top
 *  to bottom, and questions appearing and disappearing mid-form is worse than
 *  one extra question. */
export function isVisible(
  question: SurveyQuestion, answers: Record<string, string>,
): boolean {
  for (const [other, allowed] of Object.entries(question.show_if ?? {})) {
    const given = answers[other];
    if (UNANSWERED.includes(given)) continue;
    if (!allowed.includes(given as string)) return false;
  }
  return true;
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

/** What the *run* will do with this file, as opposed to what it could do.
 *
 *  `shapes` is the ranked list of candidates; `plan` is the decision. They are
 *  separate because the screen shows both — "we read this as a P&L export" and
 *  "here is what else it resembled" are different questions. */
export interface UploadPlan {
  filename: string;
  table: string;
  shape: string | null;
  confidence: number;
  mapping: Record<string, string>;
  note: string;
  read_fixes: string[];
}

export interface UploadProfile {
  filename: string;
  /** Where the server put the file. Adoption records this, not the original
   *  name — two uploads called `data.csv` are two different files. */
  stored_as?: string;
  table_key?: string;
  plan?: UploadPlan;
  read?: { rows: number; columns: string[]; notes?: string[] };
  profile?: { rows?: number; columns?: UploadColumn[]; problems?: string[] };
  shapes?: UploadShape[];
}

export interface MissingTable {
  table: string;
  unlocks_kpis: number | null;
  unlocks: string;
  supply_by: string;
  shape_id: string | null;
  example_kpis: string[];
}

export interface QualityReport {
  can_run: boolean;
  tables_present: string[];
  tables_missing: MissingTable[];
  tables_modelled: string[];
  schema_problems: string[];
  blocking: string[];
  kpis_available: number;
  kpis_blocked: number;
  plans?: UploadPlan[];
}

export interface DerivedProfile {
  values: Record<string, unknown>;
  provenance: Record<string, string>;
  notes: string[];
  still_needed: { path: string; question: string }[];
  /** Survey question ids the file could not answer. Resolved on the server:
   *  question ids and profile paths meet in `questions.py`'s `fills`, and a
   *  second implementation of that join would be one more thing to keep true. */
  remaining_questions: string[];
}

export const ingestQuality = (body: {
  uploads: string[]; answers?: Record<string, unknown>;
}) => api<QualityReport>('/api/ingest/quality', {
  method: 'POST', body: JSON.stringify(body),
});

export const ingestDerive = (body: { uploads: string[] }) =>
  api<DerivedProfile>('/api/ingest/derive', {
    method: 'POST', body: JSON.stringify(body),
  });

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
  /** Whose tables those are, or null when the union was returned. */
  archetype?: string | null;
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
/** `run_id` scopes the fact tables to that run's archetype. Without it the
 *  server answers with the union, which is right for a caller that has no run
 *  and wrong for the Studio — a retailer was being offered `mrr_movements`. */
export const getOptions = (runId?: string) =>
  api<CatalogOptions>('/api/catalog/options'
    + (runId ? `?run_id=${encodeURIComponent(runId)}` : ''));
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

export interface LogoUpload {
  logo_path: string;
  mime: string;
  bytes: number;
  data_uri: string;
}

/** Multipart for the same reason `profileUpload` is. Returns the bare name
 *  `brand.logo_path` wants, so the caller writes the response straight into
 *  the spec rather than asking the user to retype a path. */
export async function uploadLogo(file: File): Promise<LogoUpload> {
  const body = new FormData();
  body.append('file', file);
  const response = await fetch('/api/design/logo', { method: 'POST', body });
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

/** The first two pages of the real report, as a blob URL the caller must
 *  revoke. Not `api()`: the body is a PDF, not JSON. */
export async function previewPages(
  runId: string, design: unknown,
): Promise<string> {
  const response = await fetch('/api/design/preview/pages', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ run_id: runId, design }),
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* a non-JSON error body is still an error */
    }
    throw new ApiError(detail, response.status);
  }
  return URL.createObjectURL(await response.blob());
}

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
