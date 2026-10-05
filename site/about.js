import {preserveInvitationLinks} from './shared.js';

preserveInvitationLinks(new URLSearchParams(location.hash.slice(1)).get('token'));
