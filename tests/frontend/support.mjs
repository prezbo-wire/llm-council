// Shared helpers for the frontend reducer tests (not a test file itself).
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createRunState, reduceCouncilEvent } from '../../frontend/src/councilEvents.js';

export const here = dirname(fileURLToPath(import.meta.url));
export const root = join(here, '..', '..');
const fixtureDir = join(root, 'frontend', 'src', 'dev', 'fixtures');
export const fixtures = readdirSync(fixtureDir)
  .filter((f) => f.endsWith('.json'))
  .sort()
  .map((f) => JSON.parse(readFileSync(join(fixtureDir, f), 'utf8')));

export function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value);
    Object.values(value).forEach(deepFreeze);
  }
  return value;
}

/** Reduces with every intermediate state frozen, proving the reducer never mutates its input. */
export function reduceFrozen(events, start = createRunState(), now = 1000) {
  let state = deepFreeze(start);
  for (const event of events) state = deepFreeze(reduceCouncilEvent(state, deepFreeze(structuredClone(event)), now));
  return state;
}
