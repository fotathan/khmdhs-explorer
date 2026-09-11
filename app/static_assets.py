"""Static asset delivery: what the edge and the browser are allowed to keep.

Everything under /static is a FILE. It has no session, no user, and no
per-request state — and until this module existed, the app treated each one as
a full page request. Three consequences, all fixed here and by the two
short-circuits in main.py that import from this file.

1. NO COOKIE TOUCH.  AuthMiddleware puts a CSRF token into an empty session the
   first time it sees one, and Starlette's SessionMiddleware answers that with
   `Set-Cookie` — plus `Vary: Cookie` on any request that so much as READS
   `request.session`. Both landed on every /static response. Cloudflare will
   not cache a response carrying Set-Cookie, which is why live assets came back
   `CF-Cache-Status: DYNAMIC` and every visitor paid the full origin round trip
   for a font.

2. NO DATABASE.  AuthMiddleware resolves the session to a live account on every
   request — deliberately, so a deactivation bites immediately rather than at
   next login. For a signed-in reader that was one Supabase round trip per
   stylesheet, per script, per woff2, on every page view. A file has no account
   to resolve.

3. A CACHE-CONTROL THAT SAYS SOMETHING.  StaticFiles sends ETag and
   Last-Modified and nothing else, so even a warm browser pays a conditional
   request per asset. Two tiers:

     ?v=<stamp> present  ->  a year, immutable
     anything else       ->  an hour

   The stamp is what makes the year safe. `asset()` appends the deployed commit
   to every URL a template emits, so a deploy CHANGES the URL and the old one is
   never requested again — no staleness window at all. The one-hour tier is the
   fallback for URLs nothing stamps (the font binaries, which fira.css names by
   hand), where a wrong answer self-heals within the hour instead of the year.

The stamp is the commit in production and the file's own mtime in development,
so editing a stylesheet locally busts it immediately without a restart.
"""
from __future__ import annotations

import os
from urllib.parse import parse_qs

from starlette.staticfiles import StaticFiles

#: A URL that names its build may be kept forever; one that doesn't, an hour.
IMMUTABLE_MAX_AGE = 31_536_000
DEFAULT_MAX_AGE = 3_600

URL_PREFIX = "/static"

#: Render sets this to the deployed SHA. Empty everywhere else, which is the
#: signal to fall back to per-file mtimes.
_BUILD = (os.environ.get("RENDER_GIT_COMMIT") or "")[:12]


def is_static_path(path: str) -> bool:
    """True for anything the static mount serves — the one definition the
    middleware short-circuits share, so they cannot drift apart."""
    return path == URL_PREFIX or path.startswith(URL_PREFIX + "/")


def _mtime_stamp(root: str, url_path: str) -> str:
    """Development stamp: the file's own mtime, so a saved CSS edit is visible
    on the next reload. Never raises — an unstampable URL just goes unstamped
    and lands in the one-hour tier."""
    try:
        rel = url_path[len(URL_PREFIX) + 1:]
        full = os.path.normpath(os.path.join(root, rel))
        if not full.startswith(os.path.abspath(root)):
            return ""                      # outside the tree: not ours to stamp
        return format(int(os.path.getmtime(full)), "x")
    except OSError:
        return ""


def make_asset_url(root: str):
    """Build the `asset()` template global bound to this static root.

    Templates call `{{ asset('/static/css/x.css') }}` instead of writing the
    path raw. That is the whole cache-busting contract: an unstamped URL still
    works, it just gets the short tier.
    """
    root = os.path.abspath(root)

    def asset(path: str) -> str:
        if not is_static_path(path) or "?" in path:
            return path
        stamp = _BUILD or _mtime_stamp(root, path)
        return f"{path}?v={stamp}" if stamp else path

    return asset


class CachedStaticFiles(StaticFiles):
    """StaticFiles that tells the browser and the CDN how long to keep a file.

    Set on 200 and 304 alike: a 304 is the browser asking whether its copy is
    still good, and the answer must re-state the freshness or the next view
    asks again.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            if qs.get("v"):
                response.headers["Cache-Control"] = (
                    f"public, max-age={IMMUTABLE_MAX_AGE}, immutable")
            else:
                response.headers["Cache-Control"] = f"public, max-age={DEFAULT_MAX_AGE}"
        return response
