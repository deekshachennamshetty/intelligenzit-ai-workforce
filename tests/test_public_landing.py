"""Guards the public navigation fix: the landing page must render for anonymous
visitors (its own optional data fetches must never bounce them to /login.html),
while genuinely protected pages still redirect. There is no JS test runner in
this repo, so the redirect logic in client.js is guarded here as content
assertions against the real served assets, alongside the existing server-side
checks that the static routes require no authentication.
"""

from fastapi.testclient import TestClient

from backend.main import app


def _served(path: str) -> str:
    client = TestClient(app)
    response = client.get(path)
    assert response.status_code == 200, path
    return response.text


def test_landing_and_root_are_public_pages_the_client_will_not_redirect_from():
    script = _served("/assets/api/client.js")
    assert "PUBLIC_PAGES" in script
    # The set must exempt the root path ('' after splitting "/") and index.html
    # itself, in addition to the pre-existing login.html exemption, otherwise the
    # landing page's own optional authenticated fetches (401 while signed out)
    # trigger a redirect away from the page a visitor just landed on.
    assert "'login.html'" in script
    assert "''" in script
    assert "'index.html'" in script


def test_a_401_only_redirects_outside_the_public_pages():
    script = _served("/assets/api/client.js")
    assert "if (PUBLIC_PAGES.has(here)) return;" in script


def test_landing_page_primary_ctas_open_the_login_page():
    script = _served("/assets/pages/page-landing.js")
    assert "'Sign in'" in script
    assert "'Get started'" in script
    # Both primary calls to action must route to the sign-in page, not straight
    # into a protected page such as the dashboard or the new-workflow form.
    assert "href: 'login.html'" in script


def test_root_and_login_are_served_without_authentication():
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/login.html").status_code == 200


def test_protected_pages_are_still_served_at_the_page_layer_so_the_client_can_redirect():
    # The auth boundary is entirely client-side for static pages (server always
    # returns 200 so the SPA shell can load and its own script decides whether
    # to bounce to /login.html); the API layer underneath stays protected.
    client = TestClient(app)
    assert client.get("/dashboard.html").status_code == 200
    assert client.get("/api/dashboard").status_code == 401
