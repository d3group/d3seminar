let base;

export function serviceNotice(config) {
  return config.service_status === 'pending'
    ? 'Dates are provisional. Availability and talk submissions will open soon.'
    : 'Live information is temporarily unavailable. The dates shown are provisional. Please try again shortly.';
}

export function apiUrl(endpoint) {
  if (!base) throw new Error('The response service has not been configured. Please contact the organizer.');
  return new URL('api/' + endpoint, base).href;
}

export function apiFetch(endpoint, options = {}) {
  return fetch(apiUrl(endpoint), {...options, credentials:'omit', redirect:'error'});
}

export async function loadConfig() {
  base = undefined;
  const bootstrapUrl = new URL('config.json', location.href);
  const read = async url => {
    const response = await fetch(url, {cache:'no-store', credentials:'omit', redirect:'error', signal:AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error('The programme could not be loaded. Please try again.');
    return response.json();
  };
  const bootstrap = await read(bootstrapUrl);
  const address = bootstrap.api_url || bootstrap.backend_url?.replace(/api\/respond$/, '');
  if (!address) return {...bootstrap, api_url:'', backend_url:'', service_status:'pending'};
  const api = new URL(address);
  const local = api.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(api.hostname) && location.protocol !== 'https:';
  if ((api.protocol !== 'https:' && !local) || api.username || api.password || api.search || api.hash || api.pathname !== '/') {
    throw new Error('The response service needs a valid HTTPS address. Please contact the organizer.');
  }
  const liveUrl = new URL('config.json', api);
  let config;
  try {
    config = liveUrl.href === bootstrapUrl.href ? bootstrap : await read(liveUrl);
  } catch {
    return {...bootstrap, api_url:api.href, backend_url:'', service_status:'unavailable'};
  }
  base = api.href;
  return {...config, api_url:base, backend_url:apiUrl('respond'), service_status:'ready'};
}
