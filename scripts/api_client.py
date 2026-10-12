"""Shared JSON transport for the organizer and local mailer."""

import json
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise ValueError('The API redirected. Set the API URL to its final HTTPS address.')


def post_json(url, payload):
    request = Request(url, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
    try:
        response = build_opener(NoRedirect).open(request, timeout=40)
    except HTTPError as error:
        response = error
    with response:
        if 300 <= response.status < 400:
            raise ValueError('The API redirected. Set the API URL to its final HTTPS address.')
        try:
            result = json.load(response)
        except ValueError:
            raise ValueError('The backend did not return JSON. Check its address and connection.') from None
        if not isinstance(result, dict):
            raise ValueError('The backend returned an invalid response.')
        if not 200 <= response.status < 300 or result.get('ok') is not True:
            message = result.get('error')
            raise ValueError(message if isinstance(message, str) else 'The backend request failed.')
    return result
