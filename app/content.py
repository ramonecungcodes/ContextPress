"""Content loader: read site.yaml + Markdown posts, validate, render to HTML.

Content model: **Markdown with YAML frontmatter** (one `.md` file per post,
a `---` YAML block on top). Parsed with python-frontmatter. This is the
industry-standard layout (Jekyll/Hugo/Astro) and keeps a post to a single file.

Nothing here is web-framework-specific: it returns plain data structures that
the build script renders to static HTML. If a dynamic API is ever added, it can
import and reuse this exact loader.
"""
from __future__ import annotations

import re
from pathlib import Path

import frontmatter
import yaml
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from pygments import highlight as pyg_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

from .models import Post, RedirectPage, SiteConfig

WORDS_PER_MINUTE = 200


def _highlight(code: str, lang: str, _attrs: str) -> str:
    """markdown-it highlight callback -> Pygments HTML.

    Returns a full <pre class="highlight"> block; markdown-it uses it verbatim
    when the return value already starts with `<pre`.
    """
    try:
        lexer = get_lexer_by_name(lang) if lang else guess_lexer(code)
    except ClassNotFound:
        lexer = get_lexer_by_name("text")
    formatter = HtmlFormatter(nowrap=True)
    inner = pyg_highlight(code, lexer, formatter)
    lang_class = f" language-{lang}" if lang else ""
    return f'<pre class="highlight"><code class="{lang_class.strip()}">{inner}</code></pre>'


def _make_md() -> MarkdownIt:
    md = MarkdownIt(
        "commonmark",
        {"html": False, "linkify": True, "typographer": True, "highlight": _highlight},
    )
    md.enable(["table", "strikethrough", "linkify"])
    # slug ids on h2/h3 so posts can be deep-linked
    md.use(anchors_plugin, min_level=2, max_level=3)
    return md


_MD = _make_md()


def render_markdown(text: str) -> str:
    return _MD.render(text)


def pygments_css() -> str:
    """Theme-aware Pygments stylesheet: dark by default, light via media query."""
    dark = HtmlFormatter(style="github-dark").get_style_defs(".highlight")
    light = HtmlFormatter(style="default").get_style_defs(".highlight")
    return (
        "/* dark (default) */\n"
        f"{dark}\n\n"
        "/* light */\n"
        "@media (prefers-color-scheme: light) {\n"
        f"{light}\n"
        "}\n"
    )


def _reading_time(text: str) -> int:
    words = len(re.findall(r"\w+", text))
    return max(1, round(words / WORDS_PER_MINUTE))


def load_site(content_dir: Path) -> SiteConfig:
    raw = yaml.safe_load((content_dir / "site.yaml").read_text(encoding="utf-8")) or {}
    site = SiteConfig(**raw)
    site.about_html = render_markdown(site.about) if site.about else ""
    return site


def load_pages(content_dir: Path) -> list[RedirectPage]:
    """Load webroot-mirroring page definitions (`index.yaml` files).

    The content tree mirrors the webroot: every directory that holds an
    `index.yaml` produces that directory's `index.html`. content/index.yaml is
    the site root ("/"), content/ai/index.yaml is "/ai/", and so on.

    Today the only page kind is a redirect (an `index.yaml` with a `redirect:`
    key). posts/ owns its own generation (via index.md bundles) and is skipped,
    as are `_`/`.`-prefixed paths (drafts, dotfiles). An index.yaml without a
    `redirect:` key is an error, so an unsupported page kind fails loudly rather
    than shipping nothing.
    """
    pages: list[RedirectPage] = []
    for path in sorted(content_dir.rglob("index.yaml")):
        rel = path.relative_to(content_dir)
        if rel.parts[0] == "posts" or any(
            part.startswith((".", "_")) for part in rel.parts
        ):
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if "redirect" not in raw:
            raise SystemExit(
                f"{path}: page has no 'redirect:' key (the only supported "
                f"page kind); nothing to generate"
            )
        route = "/" + "/".join(rel.parts[:-1])
        if not route.endswith("/"):
            route += "/"
        pages.append(RedirectPage(route=route, **raw))
    return pages


def _scan_posts(directory: Path):
    """Yield (slug, markdown_path) for both post layouts in one directory:

    - flat file:   <slug>.md
    - page bundle: <slug>/index.md   (assets live beside index.md)

    Entries whose name starts with "_" or "." are skipped, so the _drafts/
    folder and files like .gitkeep are never treated as posts.
    """
    for entry in sorted(directory.iterdir()):
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_dir():
            index = entry / "index.md"
            if index.exists():
                yield entry.name, index
        elif entry.suffix == ".md":
            yield entry.stem, entry


def _iter_post_sources(posts_dir: Path, *, include_drafts: bool = False):
    """Yield published posts; with include_drafts, also scan posts/_drafts/.

    _drafts/ is gitignored (unfinished writing stays out of the public repo)
    and is invisible to normal builds. `build.py --drafts` pulls it in so you
    can preview work-in-progress locally.
    """
    if not posts_dir.is_dir():
        return  # a site can have no posts yet
    yield from _scan_posts(posts_dir)
    if include_drafts:
        drafts_dir = posts_dir / "_drafts"
        if drafts_dir.is_dir():
            yield from _scan_posts(drafts_dir)


def load_posts(content_dir: Path, *, include_drafts: bool = False) -> list[Post]:
    posts: list[Post] = []
    posts_dir = content_dir / "posts"
    for slug, path in _iter_post_sources(posts_dir, include_drafts=include_drafts):
        fm = frontmatter.load(path)
        meta = dict(fm.metadata)
        meta.setdefault("slug", slug)
        if meta.get("reading_time") is None:
            meta["reading_time"] = _reading_time(fm.content)
        meta["body_html"] = render_markdown(fm.content)
        post = Post(**meta)
        if post.draft and not include_drafts:
            continue
        posts.append(post)
    # newest first
    posts.sort(key=lambda p: p.date, reverse=True)
    return posts
