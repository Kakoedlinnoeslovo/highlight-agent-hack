import config from '../lib/profile-config.json' with { type: 'json' };
import { createHandler } from '../lib/profile.mjs';

export default createHandler(config);
