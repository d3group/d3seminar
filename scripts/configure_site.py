#!/usr/bin/env python3
"""Set public URLs in the GitHub Pages artifact. No private configuration is read."""
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import seminar


def configure(path):
    address = os.environ.get('D3_API_URL', '')
    api = seminar.validate_api_url(address) if address else ''
    site = seminar.validate_site_url(os.environ.get('D3_SITE_URL') or 'https://d3group.github.io/d3seminar/')
    if any(urlparse(url).scheme != 'https' or urlparse(url).hostname in ('localhost', '127.0.0.1') for url in (site, api) if url):
        raise ValueError('GitHub Pages requires public HTTPS site and API URLs.')
    config = seminar.read_json(path)
    config.update(site_url=site, api_url=api, backend_url=api + 'api/respond' if api else '')
    path.write_text(json.dumps(config, indent=2) + '\n')
    print('GitHub Pages configured with a hosted API.' if api else 'GitHub Pages configured for a read-only programme. Set D3_API_URL when the backend is ready.')


if __name__ == '__main__':
    try:
        configure(Path(__file__).resolve().parents[1] / 'site' / 'config.json')
    except ValueError as error:
        raise SystemExit(str(error)) from None
