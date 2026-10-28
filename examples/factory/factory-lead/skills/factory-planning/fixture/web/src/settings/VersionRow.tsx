import { version } from '@fx/shared';
import { api } from '@web/lib/api';

export const VersionRow = () => api(version);
