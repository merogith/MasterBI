import { useEffect, useMemo, useState } from 'preact/hooks';
import {
  createRun, getSurvey, isVisible,
  type Survey as SurveyData, type SurveyQuestion,
} from '../lib/api';
import { href, navigate } from '../lib/router';
import { Failed, Loading } from '../components/State';

/** "Use averages" is a real answer, not an absence: recording `__unknown__`
 *  makes the provenance say the field was defaulted rather than guessed. */
/** Options above which a question gets a filter box. Ten is a list you read;
 *  twenty is a list you scan and give up on, and 4.1 took the sector question
 *  from one to the other. Below this a search box is a control that never
 *  earns its place. */
const FILTER_FROM = 12;

const UNKNOWN = '__unknown__';

/* Where a half-finished survey lives.
 *
 * Nineteen questions is four minutes of someone's attention, and a reload lost
 * every one of them. `localStorage` rather than a server draft: there is no
 * account to attach a draft to, the whole survey is a few hundred bytes, and it
 * survives a closed tab — which is the case that actually loses people. */
const DRAFT_KEY = 'masterbi.survey.draft';

interface Draft {
  answers: Record<string, string>;
  name: string;
  index: number;
}

function loadDraft(): Draft | null {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Draft;
    return parsed && typeof parsed === 'object' && parsed.answers ? parsed : null;
  } catch {
    // A corrupt or unavailable store must never block the survey — private
    // browsing throws on `localStorage` in some browsers.
    return null;
  }
}

function saveDraft(draft: Draft): void {
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch { /* saving is a convenience; failing to save is not an error */ }
}

function clearDraft(): void {
  try {
    window.localStorage.removeItem(DRAFT_KEY);
  } catch { /* see above */ }
}

interface Step {
  title: string;
  questions: SurveyQuestion[];
  optional: boolean;
  review: boolean;
}

/** The steps this respondent actually has, given what they have answered.
 *
 * Recomputed on every answer, because answering "retail" removes the two
 * questions that only apply to subscription businesses. A group left with no
 * visible questions is dropped rather than shown empty. */
function buildSteps(survey: SurveyData, answers: Record<string, string>): Step[] {
  const steps: Step[] = [];
  for (const group of survey.groups) {
    const questions = survey.questions.filter(
      (q) => q.group === group && isVisible(q, answers));
    if (questions.length === 0) continue;
    steps.push({
      title: group,
      questions,
      // The deep-dive block is skippable wholesale, so the nav can offer "skip
      // this section" instead of demanding five more answers.
      optional: group === survey.optional_group,
      review: false,
    });
  }
  steps.push({ title: 'Name and review', questions: [], optional: false, review: true });
  return steps;
}

function Question({ question, answer, onAnswer }: {
  question: SurveyQuestion;
  answer: string | undefined;
  onAnswer: (value: string) => void;
}) {
  // A filter, and only where there is something to filter. Ten options is a
  // list you read; twenty is a list you scan and give up on, and the sector
  // question went from one to the other in 4.1. Below the threshold the box
  // would be a control that never earns its place.
  const [filter, setFilter] = useState('');
  const searchable = question.options.length > FILTER_FROM;
  const needle = filter.trim().toLowerCase();
  const shown = !searchable || !needle ? question.options
    : question.options.filter((option) =>
        option.value === UNKNOWN
        || option.label.toLowerCase().includes(needle)
        || (option.aliases ?? []).some((alias) => alias.includes(needle)));

  return (
    <div class="question" data-qid={question.id}>
      <div class="question-text">{question.text}</div>
      <div class="question-help">{question.help}</div>
      {question.unlocks && <div class="question-unlocks">Unlocks: {question.unlocks}</div>}
      {searchable && (
        <input class="option-filter" id={`filter-${question.id}`} type="search"
               placeholder={`Search ${question.options.length} options — try "gym", "haulage"`}
               aria-label="Filter the options"
               value={filter} onInput={(e) => setFilter(e.currentTarget.value)} />
      )}
      <div class="options">
        {shown.length === 0 && (
          <p class="empty" role="status">
            Nothing matches “{filter}”. Clear the box to see all
            {' '}{question.options.length}.
          </p>
        )}
        {shown.map((option) => {
          // `approximate` is not `disabled`. A sector that runs on the
          // cross-sector pack is a real choice with a caveat, and offering it
          // with the caveat attached beats a "Soon" pill that shows people
          // options they cannot pick.
          const classes = ['option',
            option.value === UNKNOWN ? 'unknown' : '',
            option.disabled ? 'disabled' : '',
            option.approximate ? 'approximate' : '',
            answer === option.value ? 'selected' : ''].filter(Boolean).join(' ');
          return (
            <label class={classes} key={option.value}>
              <input type="radio" name={question.id} value={option.value}
                     disabled={option.disabled} checked={answer === option.value}
                     onChange={() => onAnswer(option.value)} />
              <span>{option.label}</span>
              {option.disabled
                ? <span class="option-soon">Soon</span>
                : option.note && <span class="option-note">{option.note}</span>}
              {/* Only on the chosen one: the official classification is
                  reassurance where it is relevant and noise on nineteen rows
                  the user did not pick. */}
              {answer === option.value && option.classification && (
                <span class="option-code">{option.classification}</span>
              )}
            </label>
          );
        })}
      </div>
    </div>
  );
}

function Review({ asked, answers, name, onName, onEdit }: {
  asked: SurveyQuestion[];
  answers: Record<string, string>;
  name: string;
  onName: (value: string) => void;
  onEdit: (questionId: string) => void;
}) {
  return (
    <>
      <div class="question">
        <div class="question-text">What should we call the company?</div>
        <div class="question-help">
          Optional — we'll invent a name if you leave this blank.
        </div>
        <input class="name-input" id="company-name" maxLength={60}
               placeholder="e.g. Northwind Analytics"
               value={name} onInput={(e) => onName(e.currentTarget.value)} />
      </div>
      <div class="question">
        <div class="question-text">Your answers</div>
        <div class="question-help">
          Click any row to change it. Anything marked <em>assumed</em> will be
          filled from sector benchmarks and footnoted in the report appendix
          rather than presented as fact.
        </div>
        <div class="review-list" id="review-list">
          {asked.map((question) => {
            const value = answers[question.id];
            const answered = Boolean(value) && value !== UNKNOWN;
            const effective = answered ? value : question.default;
            const option = question.options.find((o) => o.value === effective);
            const shown = !answered && question.optional
              // An unanswered optional question is not an assumption the user
              // made — it is one we are making. Say which.
              ? 'sector benchmark'
              : `${option ? option.label : effective}${answered ? '' : ' · assumed'}`;
            return (
              <button type="button" class="review-row" key={question.id}
                      data-edit={question.id}
                      title={`Change: ${question.text}`}
                      onClick={() => onEdit(question.id)}>
                <span class="k">{question.text}</span>
                <span class={`v ${answered ? '' : 'assumed'}`}>{shown}</span>
                <span class="review-edit" aria-hidden="true">change</span>
              </button>
            );
          })}
        </div>
      </div>
    </>
  );
}

export function Survey() {
  const restored = useMemo(loadDraft, []);
  const [survey, setSurvey] = useState<SurveyData | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>(
    restored?.answers ?? {});
  const [name, setName] = useState(restored?.name ?? '');
  const [index, setIndex] = useState(restored?.index ?? 0);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(0);
  const [resumed, setResumed] = useState(Boolean(restored));

  useEffect(() => {
    getSurvey().then(setSurvey, (err: Error) => setError(err.message));
  }, []);

  const steps = useMemo(
    () => (survey ? buildSteps(survey, answers) : []), [survey, answers]);

  // Saved on every change rather than on navigation: the tab that gets closed
  // is closed mid-question, not on the way to the next step.
  useEffect(() => {
    if (survey) saveDraft({ answers, name, index });
  }, [answers, name, index, survey]);

  if (error) {
    return (
      <section class="view" id="view-survey">
        <Failed message={`Could not load the survey: ${error}`}
                onRetry={() => window.location.reload()} />
      </section>
    );
  }
  if (survey === null || steps.length === 0) {
    return (
      <section class="view" id="view-survey">
        <Loading label="Loading the questions…" />
      </section>
    );
  }

  // Branching can shorten the survey while the user is past the new end.
  const position = Math.min(index, steps.length - 1);
  const step = steps[position] as Step;
  const asked = steps.flatMap((s) => s.questions);

  // Progress is over questions answered, not steps walked. It used to be
  // `index / (steps.length - 1)`, which reads 100% on the review step — the one
  // step that still has work on it.
  const answered = asked.filter(
    (q) => answers[q.id] && answers[q.id] !== UNKNOWN).length;
  const percent = Math.round((answered / Math.max(asked.length, 1)) * 100);

  function goToQuestion(questionId: string) {
    const target = steps.findIndex(
      (s) => s.questions.some((q) => q.id === questionId));
    if (target < 0) return;
    setIndex(target);
    // The step renders before the scroll can find the row, so wait a frame.
    requestAnimationFrame(() => {
      document.querySelector(`.question[data-qid="${questionId}"]`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  }

  async function advance() {
    if (step.review) {
      try {
        const run = await createRun({
          mode: 'survey',
          answers,
          ...(name.trim() ? { company_name: name.trim() } : {}),
          seed: Math.floor(Math.random() * 1e7),
        });
        clearDraft();
        navigate(`/runs/${run.run_id}`);
      } catch (err) {
        setError((err as Error).message);
      }
      return;
    }
    // Optional questions never block progress — the derived assumption stands.
    const unanswered = step.optional
      ? [] : step.questions.filter((q) => !answers[q.id]);
    if (unanswered.length > 0) {
      setMissing(unanswered.length);
      document.querySelector(`.question[data-qid="${unanswered[0]?.id}"]`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    setMissing(0);
    setIndex(position + 1);
  }

  function skip() {
    const filled = { ...answers };
    for (const question of step.questions) {
      if (!filled[question.id]) filled[question.id] = UNKNOWN;
    }
    setAnswers(filled);
    setMissing(0);
    setIndex(position + 1);
  }

  return (
    <section class="view" id="view-survey">
      <div class="view-head">
        <a class="back" href={href('/')} onClick={(e) => { e.preventDefault(); navigate('/'); }}>
          ← Back
        </a>
        <h1>Build your own</h1>
      </div>

      {resumed && (
        <div class="notice" id="survey-resumed">
          Picked up where you left off — {answered} of {asked.length} answered.{' '}
          <button class="linkish" type="button" onClick={() => {
            clearDraft();
            setAnswers({});
            setName('');
            setIndex(0);
            setResumed(false);
          }}>Start again</button>
        </div>
      )}

      <div class="survey-progress-row">
        <div class="progress-track">
          <div class="progress-fill" id="survey-progress"
               style={{ width: `${percent}%` }} />
        </div>
        <span class="progress-label" id="survey-step-label">
          Step {position + 1} of {steps.length} · {step.title} · {answered} of
          {' '}{asked.length} answered
        </span>
      </div>

      <form id="survey-form" autocomplete="off" onSubmit={(e) => e.preventDefault()}>
        <fieldset class="survey-step active" data-step={position}>
          <legend class="survey-group-title">{step.title}</legend>
          {step.optional && (
            <p class="step-note">
              Optional. Every answer here replaces an assumption with a fact —
              skip the section and we use sector benchmarks instead, clearly
              footnoted.
            </p>
          )}
          {step.review
            ? <Review asked={asked} answers={answers} name={name} onName={setName}
                      onEdit={goToQuestion} />
            : step.questions.map((question) => (
              <Question key={question.id} question={question}
                        answer={answers[question.id]}
                        onAnswer={(value) =>
                          setAnswers({ ...answers, [question.id]: value })} />
            ))}
        </fieldset>
      </form>

      {missing > 0 && (
        <p class="warn-banner" role="alert">
          Answer {missing} more question{missing > 1 ? 's' : ''}, or use
          “Skip — use averages”.
        </p>
      )}

      <div class="survey-nav">
        <button class="ghost" id="survey-back" type="button"
                style={{ visibility: position === 0 ? 'hidden' : 'visible' }}
                onClick={() => setIndex(Math.max(0, position - 1))}>
          Back
        </button>
        <div>
          <button class="ghost" id="survey-skip" type="button" hidden={step.review}
                  onClick={skip}>
            {step.optional ? 'Skip this section' : 'Skip — use averages'}
          </button>
          <button class="primary" id="survey-next" type="button"
                  onClick={() => void advance()}>
            {step.review ? 'Generate my pack' : 'Next'}
          </button>
        </div>
      </div>
    </section>
  );
}
