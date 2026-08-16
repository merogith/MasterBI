import { useState } from 'preact/hooks';
import {
  createRun, getSurvey, ingestDerive, ingestQuality, profileUpload,
  type DerivedProfile, type QualityReport, type SurveyQuestion,
  type UploadProfile,
} from '../lib/api';
import { href, navigate } from '../lib/router';
import { QualityStep, QuestionsStep, ReadStep } from '../builder/steps';
import { Loading } from '../components/State';

/* Bring your data — a funnel, not a dead end.
 *
 * This screen used to profile a file, print a table of its columns, and offer
 * no button at all. The only way to actually use an upload was to start a
 * *synthetic* run, open the Studio, and upload the file again from the Source
 * panel — two screens and a wasted run to do one thing.
 *
 * Four steps, each reversible, in the order the Great Expectations rule asks
 * for: read it, show what was read, say what it will and will not produce, and
 * only then ask the questions the file could not answer.
 *
 *   upload -> what we read (mapping editable) -> what you will get -> questions
 *
 * "Back" is always available and never discards work, because the whole point
 * is that a person can disagree with a step and fix it.
 */
type Phase = 'upload' | 'read' | 'quality' | 'questions';

const ORDER: Phase[] = ['upload', 'read', 'quality', 'questions'];
const TITLES: Record<Phase, string> = {
  upload: 'Your file',
  read: 'What we read',
  quality: 'What you will get',
  questions: 'A few questions',
};

export function Builder() {
  const [phase, setPhase] = useState<Phase>('upload');
  const [data, setData] = useState<UploadProfile | null>(null);
  const [quality, setQuality] = useState<QualityReport | null>(null);
  const [derived, setDerived] = useState<DerivedProfile | null>(null);
  const [questions, setQuestions] = useState<SurveyQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [fill, setFill] = useState<string[]>([]);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const plan = data?.plan;
  const stored = data?.stored_as;

  async function upload(file: File) {
    setBusy('Reading and profiling…');
    setError(null);
    try {
      const profiled = await profileUpload(file);
      setData(profiled);
      setMapping({ ...(profiled.plan?.mapping ?? {}) });
      setPhase('read');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  /** The gate is computed with the *current* mapping, not the detected one, so
   *  a correction made on the previous step changes the answer here. */
  async function toQuality() {
    if (!stored) return;
    setBusy('Checking what this can produce…');
    setError(null);
    try {
      setQuality(await ingestQuality({ uploads: [stored], answers }));
      setPhase('quality');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function toQuestions() {
    if (!stored) return;
    setBusy('Reading what your file already tells us…');
    setError(null);
    try {
      const [read, survey] = await Promise.all([
        ingestDerive({ uploads: [stored] }), getSurvey(),
      ]);
      setDerived(read);
      const remaining = new Set(read.remaining_questions);
      setQuestions(survey.questions.filter((q) => remaining.has(q.id)));
      setPhase('questions');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function run() {
    if (!stored || !plan) return;
    setBusy('Starting the run…');
    setError(null);
    try {
      // The mapping goes over as a spec patch only where the user changed it.
      // Sending the detected mapping back would be redundant — the run derives
      // the same answer — and would freeze today's detection into the spec.
      const edited = Object.fromEntries(
        Object.entries(mapping).filter(
          ([field, column]) => column && column !== plan.mapping[field]));

      const created = await createRun({
        mode: 'upload',
        uploads: [stored],
        answers,
        fill_gaps: fill,
        ...(name.trim() ? { company_name: name.trim() } : {}),
        ...(Object.keys(edited).length
          ? { spec: { model: { mapping: { [plan.table]: edited } } } } : {}),
      });
      navigate(`/runs/${created.run_id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(null);
    }
  }

  const index = ORDER.indexOf(phase);
  const unanswered = questions.filter((q) => !answers[q.id]).length;

  return (
    <section class="view" id="view-builder">
      <div class="view-head">
        <a class="back" href={href('/')}
           onClick={(e) => { e.preventDefault(); navigate('/'); }}>← Back</a>
        <h1>Bring your data</h1>
        <p class="lede">Upload a CSV or Excel file. We read it the way it was
           actually written — encoding, delimiter, a title block above the real
           header — work out which fact table it is, and tell you what it will
           produce before anything runs.</p>
      </div>

      <ol class="funnel-steps">
        {ORDER.map((step, i) => (
          <li key={step} class={['funnel-step',
            i === index ? 'current' : '', i < index ? 'done' : ''].filter(Boolean).join(' ')}>
            <span class="funnel-num">{i + 1}</span>
            <span class="funnel-title">{TITLES[step]}</span>
          </li>
        ))}
      </ol>

      {error && <div class="warn-banner" role="alert">{error}</div>}
      {busy && <Loading label={busy} />}

      {phase === 'upload' && (
        <label class={`dropzone${dragging ? ' dragging' : ''}`} id="dropzone"
               onDragEnter={(e) => { e.preventDefault(); setDragging(true); }}
               onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
               onDragLeave={(e) => { e.preventDefault(); setDragging(false); }}
               onDrop={(e) => {
                 e.preventDefault();
                 setDragging(false);
                 const file = e.dataTransfer?.files?.[0];
                 if (file) void upload(file);
               }}>
          <input type="file" id="file-input" accept=".csv,.tsv,.xlsx,.xls" hidden
                 onChange={(e) => {
                   const file = e.currentTarget.files?.[0];
                   if (file) void upload(file);
                 }} />
          <span class="drop-icon" aria-hidden="true">⬆</span>
          <span class="drop-title">Drop a spreadsheet here, or click to choose</span>
          <span class="drop-sub">CSV, TSV or Excel</span>
        </label>
      )}

      {phase === 'read' && data && plan && (
        <>
          <h2 class="section-title">{data.filename}</h2>
          <p class="section-sub">
            {(data.profile?.rows ?? 0).toLocaleString()} rows ·{' '}
            {(data.profile?.columns ?? []).length} columns
          </p>
          <ReadStep data={data} plan={plan} mapping={mapping}
                    onMap={(field, column) =>
                      setMapping({ ...mapping, [field]: column })} />
        </>
      )}

      {phase === 'quality' && quality && (
        <QualityStep report={quality} fill={fill}
                     onFill={(table, on) => setFill(
                       on ? [...fill, table] : fill.filter((t) => t !== table))} />
      )}

      {phase === 'questions' && (
        <QuestionsStep questions={questions} answers={answers}
                       derivedNotes={derived?.notes ?? []}
                       name={name} onName={setName}
                       onAnswer={(id, value) =>
                         setAnswers({ ...answers, [id]: value })} />
      )}

      {phase !== 'upload' && (
        <div class="survey-nav">
          <button class="ghost" type="button"
                  onClick={() => setPhase(ORDER[index - 1] as Phase)}>
            Back
          </button>
          <div>
            {phase === 'read' && (
              <button class="primary" id="to-quality" type="button"
                      disabled={Boolean(busy)} onClick={() => void toQuality()}>
                What will this produce?
              </button>
            )}
            {phase === 'quality' && (
              <button class="primary" id="to-questions" type="button"
                      disabled={Boolean(busy) || !quality?.can_run}
                      onClick={() => void toQuestions()}>
                Continue
              </button>
            )}
            {phase === 'questions' && (
              <button class="primary" id="run-upload" type="button"
                      disabled={Boolean(busy)} onClick={() => void run()}>
                {unanswered > 0
                  ? `Generate my pack (${unanswered} unanswered)`
                  : 'Generate my pack'}
              </button>
            )}
          </div>
        </div>
      )}

      {phase === 'questions' && unanswered > 0 && (
        <p class="watch-for">
          Unanswered questions are filled from sector benchmarks and footnoted
          in the appendix — they are not guesses presented as facts.
        </p>
      )}
    </section>
  );
}
