import { useEffect, useMemo, useState } from 'preact/hooks';
import { createRun, getSurvey, type Survey as SurveyData, type SurveyQuestion } from '../lib/api';
import { navigate } from '../lib/router';

/** "Use averages" is a real answer, not an absence: recording `__unknown__`
 *  makes the provenance say the field was defaulted rather than guessed. */
const UNKNOWN = '__unknown__';

interface Step {
  title: string;
  questions: SurveyQuestion[];
  optional: boolean;
  review: boolean;
}

function buildSteps(survey: SurveyData): Step[] {
  const steps: Step[] = survey.groups.map((group) => ({
    title: group,
    questions: survey.questions.filter((q) => q.group === group),
    // The deep-dive block is skippable wholesale, so the nav can offer "skip
    // this section" instead of demanding five more answers.
    optional: group === survey.optional_group,
    review: false,
  }));
  steps.push({ title: 'Name and review', questions: [], optional: false, review: true });
  return steps;
}

function Question({ question, answer, onAnswer }: {
  question: SurveyQuestion;
  answer: string | undefined;
  onAnswer: (value: string) => void;
}) {
  return (
    <div class="question" data-qid={question.id}>
      <div class="question-text">{question.text}</div>
      <div class="question-help">{question.help}</div>
      {question.unlocks && <div class="question-unlocks">Unlocks: {question.unlocks}</div>}
      <div class="options">
        {question.options.map((option) => {
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
            </label>
          );
        })}
      </div>
    </div>
  );
}

function Review({ survey, answers, name, onName }: {
  survey: SurveyData;
  answers: Record<string, string>;
  name: string;
  onName: (value: string) => void;
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
          Anything marked <em>assumed</em> will be filled from sector benchmarks
          and footnoted in the report appendix rather than presented as fact.
        </div>
        <div class="review-list" id="review-list">
          {survey.questions.map((question) => {
            const value = answers[question.id];
            const answered = Boolean(value) && value !== UNKNOWN;
            if (!answered && question.optional) {
              // An unanswered optional question is not an assumption the user
              // made — it is one we are making. Say which.
              return (
                <div class="review-row" key={question.id}>
                  <span class="k">{question.text}</span>
                  <span class="v assumed">sector benchmark</span>
                </div>
              );
            }
            const effective = answered ? value : question.default;
            const option = question.options.find((o) => o.value === effective);
            return (
              <div class="review-row" key={question.id}>
                <span class="k">{question.text}</span>
                <span class={`v ${answered ? '' : 'assumed'}`}>
                  {option ? option.label : effective}{answered ? '' : ' · assumed'}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}

export function Survey() {
  const [survey, setSurvey] = useState<SurveyData | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [name, setName] = useState('');
  const [index, setIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(0);

  useEffect(() => {
    getSurvey().then(setSurvey, (err: Error) => setError(err.message));
  }, []);

  const steps = useMemo(() => (survey ? buildSteps(survey) : []), [survey]);

  if (error) {
    return (
      <section class="view" id="view-survey">
        <p class="empty">Could not load the survey: {error}</p>
      </section>
    );
  }
  if (survey === null || steps.length === 0) {
    return (
      <section class="view" id="view-survey">
        <p class="empty">Loading…</p>
      </section>
    );
  }

  const step = steps[index] as Step;

  async function advance() {
    if (step.review) {
      try {
        const run = await createRun({
          mode: 'survey',
          answers,
          ...(name.trim() ? { company_name: name.trim() } : {}),
          seed: Math.floor(Math.random() * 1e7),
        });
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
    setIndex(index + 1);
  }

  function skip() {
    const filled = { ...answers };
    for (const question of step.questions) {
      if (!filled[question.id]) filled[question.id] = UNKNOWN;
    }
    setAnswers(filled);
    setMissing(0);
    setIndex(index + 1);
  }

  return (
    <section class="view" id="view-survey">
      <div class="view-head">
        <a class="back" href="/" onClick={(e) => { e.preventDefault(); navigate('/'); }}>
          ← Back
        </a>
        <h1>Build your own</h1>
      </div>

      <div class="survey-progress-row">
        <div class="progress-track">
          <div class="progress-fill" id="survey-progress"
               style={{ width: `${(index / (steps.length - 1)) * 100}%` }} />
        </div>
        <span class="progress-label" id="survey-step-label">
          Step {index + 1} of {steps.length} · {step.title}
        </span>
      </div>

      <form id="survey-form" autocomplete="off" onSubmit={(e) => e.preventDefault()}>
        <fieldset class="survey-step active" data-step={index}>
          <legend class="survey-group-title">{step.title}</legend>
          {step.optional && (
            <p class="step-note">
              Optional. Every answer here replaces an assumption with a fact —
              skip the section and we use sector benchmarks instead, clearly
              footnoted.
            </p>
          )}
          {step.review
            ? <Review survey={survey} answers={answers} name={name} onName={setName} />
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
                style={{ visibility: index === 0 ? 'hidden' : 'visible' }}
                onClick={() => setIndex(Math.max(0, index - 1))}>
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
