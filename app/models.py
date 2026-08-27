"""Typed models for site config and posts.

Every YAML/frontmatter file is validated against these on load, so a
malformed post fails loudly at build time instead of shipping a broken page.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class NavItem(BaseModel):
    label: str
    href: str


class Social(BaseModel):
    github: str | None = None
    linkedin: str | None = None
    email: str | None = None


class Robots(BaseModel):
    """Crawler policy. Drives robots.txt, sitemap.xml, and a noindex meta tag."""
    allow: bool = True          # False => robots.txt blocks all + <meta noindex>
    sitemap: bool = True        # emit /sitemap.xml and reference it from robots.txt


class SiteConfig(BaseModel):
    title: str
    theme: str = "02-technical-terminal"
    tagline: str = ""
    author: str = ""
    base_url: str = ""          # e.g. https://blog.ramonecung.com (used for RSS + canonical)
    home: str = "index"         # template that renders "/": "index" or "coming-soon"
    favicon: str = ""           # optional URL override; default is the theme's favicon
    about: str = ""             # markdown; rendered to about_html at load time
    about_html: str = ""        # filled in by the loader
    nav: list[NavItem] = Field(default_factory=list)
    social: Social = Field(default_factory=Social)
    robots: Robots = Field(default_factory=Robots)


class RedirectPage(BaseModel):
    """A page whose only job is to send the browser somewhere else.

    Declared by an `index.yaml` in the content tree with a `redirect:` key.
    The file's location mirrors the webroot, so content/index.yaml -> "/" and
    content/old/index.yaml -> "/old/". Rendered to a static meta-refresh page.
    """
    kind: Literal["redirect"] = "redirect"
    redirect: str                 # target URL or path, e.g. "/ai/"
    title: str = "Redirecting"    # link text on the fallback page
    route: str = ""               # source route, filled in by the loader ("/", "/old/")


class ContentPage(BaseModel):
    """A page composed of ordered widgets, defined entirely in `index.yaml`.

    Like RedirectPage it mirrors the webroot (content/ai/index.yaml -> "/ai/").
    Each entry in `sections` is a widget: a dict that must carry a `widget:` key
    naming a template under themes/<theme>/templates/widgets/<widget>.html. The
    rest of the dict is that widget's data, passed to the template as `w`. Adding
    or reusing a widget is therefore just a template file plus a YAML block, with
    no Python change. The page chrome (topbar nav, footer) is data-driven too.
    """
    kind: Literal["content"] = "content"
    route: str = ""               # filled in by the loader
    title: str = ""               # <title> and og:title
    description: str = ""         # meta description / og:description
    og_image: str = ""            # absolute URL for social cards (optional)
    brand: str = ""               # topbar brand text (e.g. "ramon@ai-engineer: ~")
    theme_toggle: bool = True     # show the dark/light toggle in the topbar
    nav: list[NavItem] = Field(default_factory=list)          # topbar links
    footer_links: list[NavItem] = Field(default_factory=list)  # footer links
    sections: list[dict[str, Any]] = Field(default_factory=list)
    # Used when this page is aggregated by a listing (e.g. a project under a
    # projects hub): the sort key plus the card's blurb and tags.
    sort_order: int = 100
    summary: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("sections")
    @classmethod
    def _sections_name_a_widget(cls, sections):
        for i, s in enumerate(sections):
            if not isinstance(s, dict) or not isinstance(s.get("widget"), str):
                raise ValueError(
                    f"sections[{i}] must be a mapping with a string 'widget:' key"
                )
        return sections


class Post(BaseModel):
    slug: str
    title: str
    # A calendar date, optionally with a time. Posts sort newest-first by this
    # value, so two posts on the same day can add a time (`2026-08-27 14:30:00`)
    # to define their order; a bare date (`2026-08-27`) is treated as midnight.
    date: datetime
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    draft: bool = False
    reading_time: int | None = None   # minutes; auto-computed if omitted
    featured: bool = False            # highlight this post in the blog listing
    image: str = ""                   # optional cover image (bundle-relative or absolute)
    image_side: Literal["left", "right"] = "left"  # which side the cover sits on
    bundle: str = ""                  # source bundle dir (set by loader; empty for flat posts)
    body_html: str = ""               # rendered HTML (never the raw markdown)

    @field_validator("date", mode="before")
    @classmethod
    def _promote_date(cls, v: Any) -> Any:
        # YAML parses a bare `2026-08-27` to a date; promote it to midnight so
        # the field is always a datetime and same-day posts can order by time.
        if isinstance(v, date) and not isinstance(v, datetime):
            return datetime(v.year, v.month, v.day)
        return v

    @property
    def iso_date(self) -> str:
        return self.date.date().isoformat()

    @property
    def url(self) -> str:
        return f"/blog/{self.slug}/"

    @property
    def image_url(self) -> str:
        """Resolve the cover image to a site path. A bundle-relative name
        (e.g. cover.png) lives beside the post, so it resolves under the post's
        own URL; an absolute path or full URL is used as-is."""
        if not self.image or self.image.startswith(("/", "http://", "https://")):
            return self.image
        return f"{self.url}{self.image}"


class BlogPage(BaseModel):
    """A paginated listing of posts, declared by an index.yaml with a
    `list: posts` key (or a `per_page:` setting). Mirrors the webroot like the
    other page kinds (content/blog/index.yaml -> "/blog/"), and shares the same
    chrome fields as ContentPage. `per_page` splits the posts across
    /blog/, /blog/page/2/, ... Posts marked `featured` sort to the front.
    """
    kind: Literal["blog"] = "blog"
    route: str = ""
    title: str = ""
    description: str = ""
    og_image: str = ""
    brand: str = ""
    theme_toggle: bool = True
    nav: list[NavItem] = Field(default_factory=list)
    footer_links: list[NavItem] = Field(default_factory=list)
    per_page: int = Field(default=10, ge=1)
    prompt: str = ""              # section label, e.g. "ls ~/writing"
    intro: str = ""              # short blurb above the list


class ProjectsPage(BaseModel):
    """A hub that aggregates project pages, declared by an index.yaml with a
    `list: projects` key (content/projects/index.yaml -> "/projects/"). It lists
    every ContentPage directly beneath its own route (e.g. /projects/<slug>/) as
    a card, ordered by each project's `sort_order` (ascending, so 100 comes
    before 200; use 150 to slot one between). Shares the chrome fields.
    """
    kind: Literal["projects"] = "projects"
    route: str = ""
    title: str = ""
    description: str = ""
    og_image: str = ""
    brand: str = ""
    theme_toggle: bool = True
    nav: list[NavItem] = Field(default_factory=list)
    footer_links: list[NavItem] = Field(default_factory=list)
    prompt: str = ""              # section label, e.g. "ls ~/projects"
    heading: str = ""            # h1 shown above the list (e.g. "Projects")
    intro: str = ""              # lede blurb under the heading
    note: str = ""               # optional trailing note (HTML allowed)
