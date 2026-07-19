import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../../lib/api";
import type { PersonaCreatePayload, PersonaDetail, PersonaUpdatePayload } from "../../lib/types";

interface SliderField {
  key: "technical_literacy" | "anxiety" | "patience" | "curiosity";
  label: string;
  lowLabel: string;
  highLabel: string;
}

const SLIDER_FIELDS: SliderField[] = [
  {
    key: "technical_literacy",
    label: "Technical Literacy",
    lowLabel: "Needs explicit instruction",
    highLabel: "Power user",
  },
  {
    key: "anxiety",
    label: "Anxiety / Uncertainty Level",
    lowLabel: "Confident",
    highLabel: "Requires constant validation",
  },
  {
    key: "patience",
    label: "Patience Threshold",
    lowLabel: "Quick to abandon",
    highLabel: "High tolerance for friction",
  },
  {
    key: "curiosity",
    label: "Curiosity",
    lowLabel: "Sticks to the happy path",
    highLabel: "Explores freely",
  },
];

type FormState = PersonaCreatePayload;

const EMPTY_FORM: FormState = {
  slug: "",
  name: "",
  description: "",
  tech_affinity: "",
  primary_device: "",
  discovery_mode: "",
  contextual_triggers: [],
  technical_literacy: 0.5,
  anxiety: 0.5,
  patience: 0.5,
  curiosity: 0.5,
};

function toFormState(persona: PersonaDetail): FormState {
  return {
    slug: persona.slug,
    name: persona.name,
    description: persona.description,
    tech_affinity: persona.tech_affinity,
    primary_device: persona.primary_device,
    discovery_mode: persona.discovery_mode,
    contextual_triggers: persona.contextual_triggers,
    technical_literacy: persona.technical_literacy,
    anxiety: persona.anxiety,
    patience: persona.patience,
    curiosity: persona.curiosity,
  };
}

export function PersonaConfigurationPage() {
  const { personaId } = useParams<{ personaId: string }>();
  const navigate = useNavigate();
  const isNew = personaId === "new" || personaId === undefined;

  const [persona, setPersona] = useState<PersonaDetail | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [loading, setLoading] = useState(!isNew);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (isNew) return;
    api
      .getPersona(personaId!)
      .then((p) => {
        setPersona(p);
        setForm(toFormState(p));
        setLoading(false);
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load persona.");
        setLoading(false);
      });
  }, [personaId, isNew]);

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      if (isNew) {
        const created = await api.createPersona(form);
        navigate(`/predictive/personas/${created.id}`);
      } else {
        const updatePayload: PersonaUpdatePayload = {
          name: form.name,
          description: form.description,
          tech_affinity: form.tech_affinity,
          primary_device: form.primary_device,
          discovery_mode: form.discovery_mode,
          contextual_triggers: form.contextual_triggers,
          technical_literacy: form.technical_literacy,
          anxiety: form.anxiety,
          patience: form.patience,
          curiosity: form.curiosity,
        };
        const updated = await api.updatePersona(personaId!, updatePayload);
        setPersona((prev) => (prev ? { ...prev, ...updated } : prev));
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save persona.");
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    if (!persona) return;
    setError(null);
    try {
      const reset = await api.resetPersona(persona.id);
      setForm(toFormState({ ...persona, ...reset }));
      setPersona((prev) => (prev ? { ...prev, ...reset } : prev));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to reset persona.");
    }
  }

  async function handleDelete() {
    if (!persona) return;
    if (!window.confirm(`Delete persona "${persona.name}"? This can't be undone.`)) return;
    setError(null);
    try {
      await api.deletePersona(persona.id);
      navigate("/predictive");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete persona.");
    }
  }

  if (loading) {
    return <p className="text-on-surface-variant text-sm">Loading…</p>;
  }

  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <p className="text-sm text-on-surface-variant">
            Predictive Engine &gt; Persona Library &gt; {isNew ? "New Persona" : form.name}
          </p>
          <h1 className="font-headline text-3xl mt-1">
            {isNew ? "New Persona" : "Persona Configuration"}
          </h1>
          {persona?.baseline ? (
            <span className="inline-block mt-2 rounded-full bg-surface-container px-3 py-1 text-xs font-label uppercase tracking-wide">
              Baseline Persona
            </span>
          ) : null}
        </div>
        <div className="flex gap-3">
          {persona?.baseline ? (
            <button
              type="button"
              onClick={() => void handleReset()}
              className="text-sm font-medium text-primary hover:underline"
            >
              Reset Default
            </button>
          ) : null}
          {persona && !persona.baseline ? (
            <button
              type="button"
              onClick={() => void handleDelete()}
              className="text-sm font-medium text-error hover:underline"
            >
              Delete
            </button>
          ) : null}
        </div>
      </div>

      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}

      <form onSubmit={(event) => void handleSubmit(event)} className="flex flex-col gap-8">
        <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
          <h2 className="font-headline text-xl">Persona</h2>
          {isNew ? (
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-on-surface-variant">
                Slug (lowercase, hyphenated, permanent)
              </span>
              <input
                required
                pattern="^[a-z0-9]+(-[a-z0-9]+)*$"
                value={form.slug}
                onChange={(event) => update("slug", event.target.value)}
                placeholder="novice-user"
                className="ghost-border rounded-lg px-3 py-2"
              />
            </label>
          ) : null}
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Name</span>
            <input
              required
              value={form.name}
              onChange={(event) => update("name", event.target.value)}
              className="ghost-border rounded-lg px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Description</span>
            <textarea
              required
              value={form.description}
              onChange={(event) => update("description", event.target.value)}
              rows={3}
              className="ghost-border rounded-lg px-3 py-2"
            />
          </label>
        </section>

        <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
          <h2 className="font-headline text-xl">Demographic Anchors</h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-on-surface-variant">Tech Affinity</span>
              <input
                required
                value={form.tech_affinity}
                onChange={(event) => update("tech_affinity", event.target.value)}
                placeholder="Low / Medium / High"
                className="ghost-border rounded-lg px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-on-surface-variant">Primary Device</span>
              <input
                required
                value={form.primary_device}
                onChange={(event) => update("primary_device", event.target.value)}
                placeholder="Mobile / Tablet"
                className="ghost-border rounded-lg px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-on-surface-variant">Discovery Mode</span>
              <input
                required
                value={form.discovery_mode}
                onChange={(event) => update("discovery_mode", event.target.value)}
                placeholder="Search-driven"
                className="ghost-border rounded-lg px-3 py-2"
              />
            </label>
          </div>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Contextual Triggers (comma-separated)</span>
            <input
              value={form.contextual_triggers.join(", ")}
              onChange={(event) =>
                update(
                  "contextual_triggers",
                  event.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter((s) => s.length > 0),
                )
              }
              placeholder="Time Constraint, High Distraction"
              className="ghost-border rounded-lg px-3 py-2"
            />
          </label>
        </section>

        <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-6">
          <div>
            <h2 className="font-headline text-xl">Behavioral Calibration</h2>
            <p className="text-sm text-on-surface-variant mt-1">
              Adjusting these parameters alters path prediction probability.
            </p>
          </div>
          {SLIDER_FIELDS.map((field) => (
            <div key={field.key} className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">{field.label}</span>
                <span className="text-sm font-medium text-primary">
                  {Math.round(form[field.key] * 100)}%
                </span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={Math.round(form[field.key] * 100)}
                onChange={(event) => update(field.key, Number(event.target.value) / 100)}
                className="w-full"
                aria-label={field.label}
              />
              <div className="flex justify-between text-xs text-on-surface-variant">
                <span>{field.lowLabel}</span>
                <span>{field.highLabel}</span>
              </div>
            </div>
          ))}
        </section>

        <button
          type="submit"
          disabled={saving}
          className="rounded-lg bg-primary py-2.5 text-on-primary font-medium hover:opacity-90 transition disabled:opacity-50 self-start px-6"
        >
          {saving ? "Saving…" : isNew ? "Create Persona" : "Save Changes"}
        </button>
      </form>

      {!isNew && persona ? <PersonaMemoryBank persona={persona} /> : null}
    </div>
  );
}

function PersonaMemoryBank({ persona }: { persona: PersonaDetail }) {
  return (
    <section className="flex flex-col gap-4">
      <div>
        <h2 className="font-headline text-xl">Persona Memory Bank</h2>
        <p className="text-sm text-on-surface-variant mt-1">
          Notes left by retraining jobs explaining what behavioral evidence they saw and how they
          adjusted this persona.
        </p>
      </div>
      {persona.memories.length === 0 ? (
        <p className="text-on-surface-variant text-sm">
          No memory entries yet. Retraining this persona from Calibration Insights will add one.
        </p>
      ) : (
        <ul className="flex flex-col gap-3">
          {persona.memories.map((memory) => (
            <li key={memory.id} className="ghost-border rounded-lg p-4">
              <div className="flex items-center justify-between gap-3">
                <p className="font-medium">{memory.title}</p>
                <p className="text-xs text-on-surface-variant whitespace-nowrap">
                  {new Date(memory.created_at).toLocaleDateString()}
                </p>
              </div>
              <p className="text-sm text-on-surface-variant mt-1">{memory.note}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
