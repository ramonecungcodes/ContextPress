"""Typed models for site config and posts.

Every YAML/frontmatter file is validated against these on load, so a
malformed post fails loudly at build time instead of shipping a broken page.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


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


class Post(BaseModel):
    slug: str
    title: str
    date: date
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    draft: bool = False
    reading_time: int | None = None   # minutes; auto-computed if omitted
    body_html: str = ""               # rendered HTML (never the raw markdown)

    @property
    def iso_date(self) -> str:
        return self.date.isoformat()

    @property
    def url(self) -> str:
        return f"/posts/{self.slug}/"
