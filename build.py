#!/usr/bin/env python3
"""ContextPress static site generator.

Reads content/ (site.yaml + Markdown posts), renders the Jinja2 templates,
and writes a fully static site to dist/ that any web server (here: nginx) can
serve directly. No server-side runtime is required to view the site.

Usage:
    python build.py [--drafts] [--out dist] [--content content]
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import re
import shutil
import socketserver
import webbrowser
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from jinja2 import Environment, FileSystemLoader, select_autoescape
from jinja2.exceptions import TemplateNotFound

from app.content import (
    load_pages,
    load_posts,
    load_site,
    pygments_css,
    render_markdown,
)

ROOT = Path(__file__).parent


def build(content_dir: Path, out_dir: Path, themes_dir: Path,
          *, drafts: bool = False, base_url: str | None = None) -> None:
    site = load_site(content_dir)

    # base_url is the canonical production URL (site.yaml). A build-time override
    # (e.g. `--dev` -> "", or `--base-url https://staging...`) wins over it, so
    # which environment we build for is decided by the command, not the content.
    if base_url is not None:
        site.base_url = base_url
    base = site.base_url.rstrip("/")   # "" => relative (document-relative) build

    # A theme bundles its own templates + static assets under themes/<name>/.
    # The `theme` field in site.yaml selects which bundle to render with.
    theme_dir = themes_dir / site.theme
    templates_dir = theme_dir / "templates"
    static_dir = theme_dir / "static"
    if not templates_dir.is_dir():
        raise SystemExit(
            f"Theme '{site.theme}' not found: expected templates at {templates_dir}"
        )

    posts = load_posts(content_dir, include_drafts=drafts)
    pages = load_pages(content_dir)

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["now_year"] = datetime.now(timezone.utc).year
    # `{{ text | markdown }}` renders a markdown string (with @@FIG figures) to
    # HTML, for the markdown widget and any rich body in a widget page.
    env.filters["markdown"] = render_markdown

    # Fresh output. Clear the directory's *contents* rather than removing the
    # directory itself, so this works when out_dir is a bind mount (removing a
    # mount point fails with "device or resource busy").
    out_dir.mkdir(parents=True, exist_ok=True)
    for child in out_dir.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()

    # static assets
    if static_dir.exists():
        shutil.copytree(static_dir, out_dir / "static")
    css_dir = out_dir / "static" / "css"
    css_dir.mkdir(parents=True, exist_ok=True)
    (css_dir / "pygments.css").write_text(pygments_css(), encoding="utf-8")

    # Cache-busting: append a short content hash to static URLs so browsers
    # fetch the new file the instant it changes (while /static/ can be cached
    # aggressively). Defined after assets are written so the hash is current.
    def static_url(rel: str) -> str:
        f = out_dir / "static" / rel
        if not f.is_file() or not base:
            # No cache-buster in relative (dev/file://) builds: it's a
            # production concern, and a query string can confuse file:// loads.
            return f"/static/{rel}"
        digest = hashlib.sha1(f.read_bytes()).hexdigest()[:8]
        return f"/static/{rel}?v={digest}"

    env.globals["static_url"] = static_url

    # webroot-mirroring pages: redirects and widget-composed content pages.
    # A page at "/" overrides the home template below (index.yaml wins over
    # site.home).
    redirect_tmpl = env.get_template("redirect.html")
    page_tmpl = env.get_template("page.html")
    blog_tmpl = env.get_template("blog.html")
    projects_tmpl = env.get_template("projects.html")

    def emit(dest: Path, html: str) -> None:
        """Write an HTML document, first qualifying its internal links. `depth`
        is how many directories below dist the page sits, so links can be made
        document-relative when no base_url is set."""
        depth = len(dest.relative_to(out_dir).parts) - 1
        dest.write_text(qualify_links(html, base, depth), encoding="utf-8")

    routes = {p.route for p in pages}
    for page in pages:
        dest = out_dir / _route_to_index(page.route)
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Page bundle: copy sibling files (images, downloads) into the page's
        # output dir so its links resolve. Runs for every page kind, so a root
        # asset like content/headshot.jpg ships to dist/headshot.jpg (beside the
        # root redirect). index.yaml/site.yaml are skipped; a subdirectory that
        # is itself a route (has its own index.yaml) is left for that route to
        # emit, but a plain asset dir (e.g. img/) is copied wholesale.
        src_bundle = content_dir / _route_to_index(page.route).parent
        if src_bundle.is_dir():
            for asset in src_bundle.iterdir():
                if asset.name in ("index.yaml", "site.yaml"):
                    continue
                if asset.is_dir():
                    if (asset / "index.yaml").exists():
                        continue  # a nested route owns this directory
                    shutil.copytree(asset, dest.parent / asset.name, dirs_exist_ok=True)
                elif asset.is_file():
                    shutil.copy2(asset, dest.parent / asset.name)

        if page.kind == "redirect":
            target = page.redirect
            canonical = base + target if base and target.startswith("/") else target
            emit(dest, redirect_tmpl.render(site=site, page=page, target=target,
                                            canonical=canonical))
        elif page.kind == "blog":
            _emit_blog(page, posts, out_dir, base, blog_tmpl, site, emit)
        elif page.kind == "projects":
            projects = _child_projects(page.route, pages)
            canonical = base + page.route if base else page.route
            emit(dest, projects_tmpl.render(site=site, page=page,
                                            projects=projects, canonical=canonical))
        else:  # content page
            canonical = base + page.route if base else page.route
            emit(dest, page_tmpl.render(site=site, page=page, posts=posts,
                                        canonical=canonical))

    # home page ("/"): the template named by site.home ("index" or "coming-soon"),
    # unless a redirect page already claimed "/".
    if "/" not in routes:
        try:
            home_tmpl = env.get_template(f"{site.home}.html")
        except TemplateNotFound:
            raise SystemExit(
                f"home '{site.home}' has no template: expected "
                f"{templates_dir / (site.home + '.html')}"
            )
        emit(out_dir / "index.html", home_tmpl.render(site=site, posts=posts))

    # posts, served at /blog/<slug>/ (slug from frontmatter, may differ from the
    # source folder name)
    post_tmpl = env.get_template("post.html")
    for post in posts:
        post_dir = out_dir / "blog" / post.slug
        post_dir.mkdir(parents=True, exist_ok=True)

        # Page bundle: copy assets that live beside the post's index.md (images
        # in img/, downloads) so relative links in the markdown resolve.
        # post.bundle is the source dir (set by the loader; empty for flat posts).
        if post.bundle:
            src_bundle = Path(post.bundle)
            for asset in src_bundle.iterdir():
                if asset.name == "index.md":
                    continue
                if asset.is_dir():
                    shutil.copytree(asset, post_dir / asset.name, dirs_exist_ok=True)
                elif asset.is_file():
                    shutil.copy2(asset, post_dir / asset.name)

        emit(post_dir / "index.html", post_tmpl.render(site=site, post=post))

    # Raw HTML pages: any *.html in the content tree is copied to its mirrored
    # dist path verbatim (with internal links qualified like everything else),
    # for hand-authored / snapshot pages that already carry their own markup
    # (e.g. dated coverage reports). posts/ and _/.-prefixed paths are skipped.
    raw_html = 0
    for html_path in sorted(content_dir.rglob("*.html")):
        rel = html_path.relative_to(content_dir)
        if rel.parts[0] == "posts" or any(p.startswith((".", "_")) for p in rel.parts):
            continue
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        emit(dest, html_path.read_text(encoding="utf-8"))
        raw_html += 1

    # RSS
    (out_dir / "feed.xml").write_text(_rss(site, posts), encoding="utf-8")

    # robots.txt + sitemap.xml (crawler policy from site.robots)
    (out_dir / "robots.txt").write_text(_robots(site), encoding="utf-8")
    if site.robots.sitemap:
        (out_dir / "sitemap.xml").write_text(_sitemap(site, posts), encoding="utf-8")

    home_desc = "redirect" if "/" in routes else site.home
    print(f"Built {len(posts)} post(s), {len(pages)} page(s), {raw_html} raw -> {out_dir}  "
          f"(home={home_desc}, base_url={site.base_url or 'relative'}, "
          f"crawlers={'allowed' if site.robots.allow else 'blocked'})")


def _route_to_index(route: str) -> Path:
    """Map a route to its static output path: "/" -> index.html,
    "/ai/" -> ai/index.html."""
    parts = [p for p in route.strip("/").split("/") if p]
    return Path(*parts, "index.html") if parts else Path("index.html")


def _child_projects(hub_route, pages):
    """Content pages directly beneath `hub_route` (e.g. /projects/<slug>/),
    ordered by sort_order then title, for a projects hub to list as cards."""
    kids = [
        p for p in pages
        if getattr(p, "kind", None) == "content"
        and p.route != hub_route
        and p.route.startswith(hub_route)
        and "/" not in p.route[len(hub_route):].strip("/")
    ]
    return sorted(kids, key=lambda p: (p.sort_order, p.title))


def _emit_blog(page, posts, out_dir, base, blog_tmpl, site, emit):
    """Generate the paginated blog listing for a BlogPage.

    Posts are ordered featured-first (each group keeps its incoming order, which
    is newest-first) and split into pages of page.per_page. Page 1 is page.route
    ("/blog/"); page N (>=2) is "<route>page/N/". Prev/next links stitch them.
    """
    featured = [p for p in posts if p.featured]
    rest = [p for p in posts if not p.featured]
    ordered = featured + rest
    per = page.per_page
    total_pages = max(1, (len(ordered) + per - 1) // per)

    for i in range(total_pages):
        n = i + 1
        chunk = ordered[i * per:(i + 1) * per]
        route = page.route if n == 1 else f"{page.route}page/{n}/"
        dest = out_dir / _route_to_index(route)
        dest.parent.mkdir(parents=True, exist_ok=True)

        prev_url = None
        if n == 2:
            prev_url = page.route
        elif n > 2:
            prev_url = f"{page.route}page/{n - 1}/"
        next_url = f"{page.route}page/{n + 1}/" if n < total_pages else None

        title = page.title if n == 1 else f"{page.title} · page {n}"
        canonical = (base + route) if base else route
        emit(dest, blog_tmpl.render(
            site=site, page=page, posts=chunk, title=title,
            page_num=n, total_pages=total_pages,
            prev_url=prev_url, next_url=next_url, canonical=canonical,
        ))


# href/src values that must NOT be rewritten: external URLs, protocol-relative
# URLs, in-page fragments, and non-navigational schemes.
_LINK_SKIP = ("http://", "https://", "//", "#", "mailto:", "tel:", "sms:",
              "data:", "javascript:")
_LINK_ATTR = re.compile(r'\b(href|src)="([^"]*)"')


def qualify_links(html: str, base_url: str, depth: int) -> str:
    """Rewrite site-absolute href/src values (those starting with "/") so
    internal links resolve wherever the page is served from.

    With a base_url set, links become fully qualified: "/ai/" -> "https://host/
    ai/". With an empty base_url they become document-relative to the page's own
    location using `depth` (how many directories deep the page is): from "/ai/"
    (depth 1) "/static/x" -> "../static/x"; from the root (depth 0) -> "./static/
    x". Document-relative output works under file:// and any subpath, not just a
    server root.

    External links (http/https), protocol-relative "//", fragments "#...", and
    mailto:/tel:/data:/javascript: are left untouched, as are page-relative links
    (no leading slash). Code samples are safe: markdown renders their quotes as
    &quot;, so only real attribute quotes (") match.
    """
    base = base_url.rstrip("/")
    rel_prefix = "../" * depth if depth else "./"

    def repl(m: re.Match) -> str:
        attr, val = m.group(1), m.group(2)
        if not val or not val.startswith("/") or val.startswith(_LINK_SKIP):
            return m.group(0)
        newval = f"{base}{val}" if base else rel_prefix + val[1:]
        return f'{attr}="{newval}"'

    return _LINK_ATTR.sub(repl, html)


def _robots(site) -> str:
    base = site.base_url.rstrip("/")
    lines = ["User-agent: *"]
    if site.robots.allow:
        lines.append("Allow: /")
        if site.robots.sitemap and base:
            lines.append(f"Sitemap: {base}/sitemap.xml")
    else:
        lines.append("Disallow: /")
    return "\n".join(lines) + "\n"


def _sitemap(site, posts) -> str:
    base = site.base_url.rstrip("/")
    entries = [("/", None)] + [(p.url, p.iso_date) for p in posts]
    items = []
    for loc, lastmod in entries:
        row = f"  <url>\n    <loc>{escape(base + loc)}</loc>\n"
        if lastmod:
            row += f"    <lastmod>{lastmod}</lastmod>\n"
        row += "  </url>"
        items.append(row)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(items) + "\n"
        "</urlset>\n"
    )


def _rss(site, posts) -> str:
    base = site.base_url.rstrip("/")
    now = format_datetime(datetime.now(timezone.utc))
    items = []
    for p in posts:
        link = f"{base}{p.url}"
        pub = format_datetime(datetime(p.date.year, p.date.month, p.date.day,
                                       tzinfo=timezone.utc))
        items.append(
            "    <item>\n"
            f"      <title>{escape(p.title)}</title>\n"
            f"      <link>{escape(link)}</link>\n"
            f"      <guid isPermaLink=\"true\">{escape(link)}</guid>\n"
            f"      <pubDate>{pub}</pubDate>\n"
            f"      <description>{escape(p.description)}</description>\n"
            "    </item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "  <channel>\n"
        f"    <title>{escape(site.title)}</title>\n"
        f"    <link>{escape(base + '/')}</link>\n"
        f"    <description>{escape(site.tagline)}</description>\n"
        f"    <lastBuildDate>{now}</lastBuildDate>\n"
        + "\n".join(items) + "\n"
        "  </channel>\n"
        "</rss>\n"
    )


def serve(directory: Path, port: int, *, open_browser: bool = True) -> None:
    """Serve `directory` over HTTP and (optionally) open a browser to it.

    Blocks until interrupted (Ctrl+C). If `port` is taken, tries the next few.
    """
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(directory))
    httpd = None
    for candidate in range(port, port + 10):
        try:
            httpd = socketserver.TCPServer(("", candidate), handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit(f"Could not bind a port in {port}..{port + 9}")

    url = f"http://localhost:{port}/"
    print(f"Serving {directory} at {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the ContextPress static site.")
    ap.add_argument("--drafts", action="store_true", help="include draft posts")
    ap.add_argument("--out", default="dist", help="output directory (default: dist)")
    ap.add_argument("--content", default="content", help="content directory")
    ap.add_argument("--themes", default="themes", help="themes directory")
    # base_url override: default is site.yaml's canonical (prod) URL. These pick
    # the build target from the command instead. --dev builds relative and then
    # serves dist + opens a browser; --base-url "" is the same relative build
    # without the server (for file:// or zipping).
    target = ap.add_mutually_exclusive_group()
    target.add_argument("--base-url", dest="base_url", default=None,
                        help="override site.yaml base_url (e.g. a staging domain)")
    target.add_argument("--dev", action="store_true",
                        help="relative build, then serve dist and open a browser")
    ap.add_argument("--port", type=int, default=8000,
                    help="dev server port (default: 8000)")
    ap.add_argument("--no-open", action="store_true",
                    help="with --dev, do not open a browser")
    args = ap.parse_args()

    out_dir = ROOT / args.out
    build(
        content_dir=ROOT / args.content,
        out_dir=out_dir,
        themes_dir=ROOT / args.themes,
        drafts=args.drafts,
        base_url="" if args.dev else args.base_url,
    )
    if args.dev:
        serve(out_dir, args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
