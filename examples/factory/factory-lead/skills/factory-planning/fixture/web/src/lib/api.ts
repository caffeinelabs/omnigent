import type { route } from '../../../services/api/src/routes/version';

export type Route = typeof route;
export const api = (value: string) => value;
