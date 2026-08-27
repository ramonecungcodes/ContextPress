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
import hashlib
import re
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from jinja2 import Environment, FileSystemLoader, select_autoescape
from jinja2.exceptions import TemplateNotFound

from app.content import load_pages, load_posts, load_site, pygments_css

ROOT = Path(__file__).parent


def build(content_dir: Path, out_dir: Path, themes_dir: Path,
          *, drafts: bool = False) -> None:
    site = load_site(content_dir)

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
        if not f.is_file():
            return f"/static/{rel}"
        digest = hashlib.sha1(f.read_bytes()).hexdigest()[:8]
        return f"/static/{rel}?v={digest}"

    env.globals["static_url"] = static_url

    # webroot-mirroring pages: redirects and widget-composed content pages.
    # A page at "/" overrides the home template below (index.yaml wins over
    # site.home).
    redirect_tmpl = env.get_template("redirect.html")
    page_tmpl = env.get_template("page.html")
    base = site.base_url.rstrip("/")

    def emit(dest: Path, html: str) -> None:
        """Write an HTML document, first qualifying its internal links."""
        dest.write_text(absolutize_links(html, base), encoding="utf-8")

    routes = {p.route for p in pages}
    for page in pages:
        dest = out_dir / _route_to_index(page.route)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if page.kind == "redirect":
            target = page.redirect
            canonical = base + target if base and target.startswith("/") else target
            emit(dest, redirect_tmpl.render(site=site, page=page, target=target,
                                            canonical=canonical))
        else:  # content page
            # Page bundle: copy any assets that live beside index.yaml (images,
            # downloads) so relative links in the page resolve.
            src_bundle = content_dir / _route_to_index(page.route).parent
            if src_bundle.is_dir():
                for asset in src_bundle.iterdir():
                    if asset.is_file() and asset.name != "index.yaml":
                        shutil.copy2(asset, dest.parent / asset.name)
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

    # posts
    post_tmpl = env.get_template("post.html")
    for post in posts:
        post_dir = out_dir / "posts" / post.slug
        post_dir.mkdir(parents=True, exist_ok=True)

        # Page bundle: copy assets that live beside the post's index.md
        # (images, downloads) so relative links in the markdown resolve.
        # A post is a bundle if posts/<slug>/ exists (or posts/_drafts/<slug>/
        # for a draft being previewed with --drafts).
        src_bundle = content_dir / "posts" / post.slug
        if not src_bundle.is_dir():
            src_bundle = content_dir / "posts" / "_drafts" / post.slug
        if src_bundle.is_dir():
            for asset in src_bundle.iterdir():
                if asset.is_file() and asset.name != "index.md":
                    shutil.copy2(asset, post_dir / asset.name)

        emit(post_dir / "index.html", post_tmpl.render(site=site, post=post))

    # RSS
    (out_dir / "feed.xml").write_text(_rss(site, posts), encoding="utf-8")

    # robots.txt + sitemap.xml (crawler policy from site.robots)
    (out_dir / "robots.txt").write_text(_robots(site), encoding="utf-8")
    if site.robots.sitemap:
        (out_dir / "sitemap.xml").write_text(_sitemap(site, posts), encoding="utf-8")

    home_desc = "redirect" if "/" in routes else site.home
    print(f"Built {len(posts)} post(s), {len(pages)} page(s) -> {out_dir}  "
          f"(home={home_desc}, "
          f"crawlers={'allowed' if site.robots.allow else 'blocked'})")


def _route_to_index(route: str) -> Path:
    """Map a route to its static output path: "/" -> index.html,
    "/ai/" -> ai/index.html."""
    parts = [p for p in route.strip("/").split("/") if p]
    return Path(*parts, "index.html") if parts else Path("index.html")


# href/src values that must NOT be prefixed with base_url: external URLs,
# protocol-relative URLs, in-page fragments, and non-navigational schemes.
_LINK_SKIP = ("http://", "https://", "//", "#", "mailto:", "tel:", "sms:",
              "data:", "javascript:")
_LINK_ATTR = re.compile(r'\b(href|src)="([^"]*)"')


def absolutize_links(html: str, base_url: str) -> str:
    """Prefix site-absolute links with base_url so internal links are fully
    qualified. Rewrites only root-relative href/src values (those starting with
    "/"): "/ai/" -> "https://host/ai/". External links (http/https), protocol-
    relative "//", fragments "#...", and mailto:/tel:/data:/javascript: are left
    untouched, as are page-relative links (no leading slash), which can't be
    resolved without the page's own path. A no-op when base_url is empty.

    Code samples are safe: markdown renders their quotes as &quot;, so only real
    attribute quotes (") match.
    """
    if not base_url:
        return html
    base = base_url.rstrip("/")

    def repl(m: re.Match) -> str:
        attr, val = m.group(1), m.group(2)
        if not val or not val.startswith("/") or val.startswith(_LINK_SKIP):
            return m.group(0)
        return f'{attr}="{base}{val}"'

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


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the ContextPress static site.")
    ap.add_argument("--drafts", action="store_true", help="include draft posts")
    ap.add_argument("--out", default="dist", help="output directory (default: dist)")
    ap.add_argument("--content", default="content", help="content directory")
    ap.add_argument("--themes", default="themes", help="themes directory")
    args = ap.parse_args()

    build(
        content_dir=ROOT / args.content,
        out_dir=ROOT / args.out,
        themes_dir=ROOT / args.themes,
        drafts=args.drafts,
    )


if __name__ == "__main__":
    main()
