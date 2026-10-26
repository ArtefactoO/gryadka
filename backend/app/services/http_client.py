"""HTTP-only transport boundary shared by configured infrastructure collectors."""
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import Request, HTTPRedirectHandler, HTTPSHandler, build_opener
from functools import lru_cache


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, 'Collector redirects are not allowed', headers, fp)


@lru_cache(maxsize=1)
def _default_opener():
    # HTTPSHandler loads the CA store even for plain HTTP. Reuse this stateless
    # transport; Authorization stays on each Request, never on the opener.
    return build_opener(_RejectRedirects(), HTTPSHandler())


def open_http(request, *args, **kwargs):
    url = request.full_url if isinstance(request, Request) else str(request)
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Collector endpoint must be HTTP(S) without embedded credentials')
    context = kwargs.pop('context', None)
    # Configured endpoints are the boundary. Never forward credentials or requests
    # to a Location chosen by a remote service, including HTTPS → HTTP redirects.
    opener = _default_opener() if context is None else build_opener(_RejectRedirects(), HTTPSHandler(context=context))
    return opener.open(request, *args, **kwargs)
