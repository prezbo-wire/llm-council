/**
 * Helpers for merging an in-flight turn into a conversation's messages (pure).
 *
 * The backend may persist the user message when the run starts, so a conversation reloaded
 * mid-run can already end with it; these helpers keep the UI from showing it twice.
 */

/** True when `messages` already ends with the pending user message. */
export function endsWithUserMessage(messages, content) {
  if (!Array.isArray(messages) || messages.length === 0) return false;
  const last = messages[messages.length - 1];
  return Boolean(last && last.role === 'user' && last.content === content);
}

/** Appends the finished turn (user message once, then the assistant message). */
export function appendTurn(messages, content, assistantMessage) {
  const base = Array.isArray(messages) ? messages : [];
  const withUser = endsWithUserMessage(base, content) ? base : [...base, { role: 'user', content }];
  return [...withUser, assistantMessage];
}
