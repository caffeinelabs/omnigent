// import { gone } from './gone';
/* import { alsoGone } from './also-gone'; */
import { version } from '../../../packages/shared/src/version';

export function migrate(): string {
  return `schema for ${version}`;
}
