/**
 * ChatGPT plan billing (Mese `mese.billing`, ARCHITECTURE §F.3; OpenAI's Sign in with ChatGPT UX guidelines).
 *
 * `chatgpt/*` models are billed to the user's own ChatGPT plan: Mese reports `cost_usd: 0` with
 * `cost_source: 'plan'`, but each call counts toward the plan's usage limits. At a limit Mese answers
 * `plan_limit_exceeded` and pauses every `chatgpt/*` model; it never falls back to API-key billing, and
 * llm-council never swaps a plan model for its API-key twin (`openai/*`) or the reverse.
 * Pure constants and helpers (no React), shared by the badge, the picker, the cards and the cost labels.
 */

/** `CouncilModel.billing` of a plan-billed model; the other values are 'api_key' and 'local'. */
export const PLAN_BILLING = 'chatgpt_plan';
/** Plan models are exactly the `chatgpt/<slug>` ids (Mese spec D1). */
export const PLAN_ID_PREFIX = 'chatgpt/';
/** OpenAI's "Manage usage" destination for a ChatGPT plan. */
export const MANAGE_USAGE_URL = 'https://chatgpt.com/settings/usage';
export const MANAGE_USAGE = Object.freeze({ label: 'Manage usage', href: MANAGE_USAGE_URL });
/** Shown near the model picker while a plan model is in the council. */
export const PLAN_NOTE = 'Using ChatGPT plan';
/** The usage-limit message, verbatim from the UX guidelines (title / detail). */
export const PLAN_LIMIT_TITLE = 'Usage limit reached';
export const PLAN_LIMIT_DETAIL = "Review your plan or this app's limit in ChatGPT settings.";

/**
 * True for a model billed to the ChatGPT plan. Takes a CouncilModel or a ModelInfo (anything with `id` and
 * `billing`). Mese's `billing` decides when it is known; without it (an older Mese, an id that left the
 * catalog and has no snapshot billing) the `chatgpt/` prefix does.
 */
export function isPlanBilled(model) {
  if (!model || typeof model !== 'object') return false;
  if (typeof model.billing === 'string' && model.billing) return model.billing === PLAN_BILLING;
  return typeof model.id === 'string' && model.id.startsWith(PLAN_ID_PREFIX);
}
