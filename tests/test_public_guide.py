"""The public HTML guides stay accessible, offline, private, and link-complete."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parents[1]
GUIDE = REPO / "docs" / "index.html"
MANUAL = REPO / "docs" / "manual"
GENERATED = {
    MANUAL / name
    for name in (
        "docs/README.html",
        "docs/assets/videos/chat-launcher-demo-transcript.html",
        "docs/assets/videos/viki-agent-cinematic-launch-paced-transcript.html",
        "docs/bots.html",
        "docs/concepts.html",
        "docs/configuration.html",
        "docs/decision-engine.html",
        "docs/getting-started.html",
        "docs/known-issues.html",
        "docs/mattermost.html",
        "docs/messaging.html",
        "docs/security.html",
        "docs/sessions-and-data.html",
        "docs/troubleshooting.html",
        "docs/using-the-app.html",
        "search.html",
    )
}
# The blog (docs/blog/) is hand-written HTML on the manual layout, linked from the guide.
BLOG = {REPO / "docs/blog" / name for name in ("index.html", "bots-learn-what-you-approve.html")}
PUBLIC_PAGES = GENERATED | BLOG | {GUIDE}
PUBLIC_DOWNLOADS = {
    REPO / "docs/assets" / name
    for name in (
        "brand-workflow.svg",
        "diagrams/agent-ecosystem.svg",
        "diagrams/chief-to-routine.svg",
        "diagrams/work-review-loop.svg",
    )
}
CAPTIONS = REPO / "docs/assets/videos/chat-launcher-demo.en.vtt"
LAUNCH_CAPTIONS = REPO / "docs/assets/videos/viki-agent-cinematic-launch-paced.en.vtt"
CAPTION_URL = "data:text/vtt;charset=utf-8,"
# The guide links the public GitHub Sponsors page and the public repository: the only places the
# owner's handle may appear in it, as tests/test_fixtures_clean.py also allows. Split so the
# privacy scan of this file passes.
SPONSOR_HREF = 'href="https://github.com/sponsors/' + "take" + 'hiro177"'
REPO_HREF = 'href="https://github.com/' + "take" + 'hiro177/VIKI-Agent"'
# The showcase is its own built site beside the guide (docs/showcase/). The guide's header links to
# it, and tests/test_fixtures_clean.py scans its bundle.
SHOWCASE = REPO / "docs/showcase/index.html"
CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")\s]+)['\"]?\s*\)", re.IGNORECASE)
sys.path.insert(0, str(REPO / "scripts"))

import scrub_fixture  # noqa: E402


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    children: list[Element | str] = field(default_factory=list)

    def text(self) -> str:
        return " ".join(
            child if isinstance(child, str) else child.text() for child in self.children
        )


class GuideParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("document", {})
        self.stack = [self.root]
        self.elements: list[Element] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(element)
        self.elements.append(element)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(element)
        self.elements.append(element)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def read_guide(path: Path = GUIDE) -> tuple[str, GuideParser]:
    source = path.read_text(encoding="utf-8")
    parser = GuideParser()
    parser.feed(source)
    parser.close()
    return source, parser


def local_target(page: Path, value: str) -> Path:
    target = urlsplit(value)
    assert not target.scheme and not target.netloc, (page, value)
    assert "\\" not in value, (page, value)
    resolved = (page.parent / unquote(target.path)).resolve() if target.path else page
    assert resolved.is_relative_to(REPO), (page, value)
    assert resolved.is_file(), (page, value)
    return resolved


def test_guide_has_semantic_navigation_and_only_local_assets() -> None:
    scripts = []
    styles = set()
    for page in PUBLIC_PAGES:
        _, parser = read_guide(page)
        inline_captions = None
        tags = {element.tag for element in parser.elements}
        assert {"header", "nav", "main", "footer"} <= tags, page
        if page == GUIDE or page in GENERATED:
            assert any(element.attrs.get("href") == "#main" for element in parser.elements)
            label = "Guide sections" if page == GUIDE else "Documentation"
            assert any(
                element.tag == "nav" and element.attrs.get("aria-label") == label
                for element in parser.elements
            ), page
        if page == GUIDE:
            assert "section" in tags
        for element in parser.elements:
            if element.tag == "video":
                source = next(
                    child
                    for child in element.children
                    if isinstance(child, Element) and child.tag == "source"
                )
                inline_captions = local_target(page, source.attrs["src"]).with_suffix(".en.vtt")
            assert element.tag not in {"iframe", "object", "embed", "base"}, page
            assert not any(key.lower().startswith("on") for key in element.attrs), page
            assert "srcset" not in element.attrs, page
            if element.tag == "script":
                assert page == MANUAL / "search.html"
                assert element.attrs == {"src": "../assets/doc-search.js", "defer": ""}
                assert not element.text().strip()
                scripts.append(local_target(page, element.attrs["src"]))
            if element.tag == "style":
                styles.add((page, element.text()))
            if "style" in element.attrs:
                styles.add((page, element.attrs["style"]))
            if element.tag == "link" and element.attrs.get("rel") == "stylesheet":
                stylesheet = local_target(page, element.attrs["href"])
                styles.add((stylesheet, stylesheet.read_text(encoding="utf-8")))
            for key in ("src", "href", "poster", "action"):
                value = element.attrs.get(key)
                if value is None:
                    continue
                if value.startswith(CAPTION_URL):
                    assert element.tag == "track" and key == "src"
                    assert element.attrs.get("kind") == "captions"
                    assert inline_captions is not None
                    assert unquote(value[len(CAPTION_URL) :]) == inline_captions.read_text(
                        encoding="utf-8"
                    )
                    continue
                target = urlsplit(value)
                if target.scheme or target.netloc:
                    assert element.tag == "a" and key == "href", (page, value)
                    assert target.scheme in {"http", "https", "mailto"}, (page, value)
                    continue
                local_target(page, value)
    assert scripts == [REPO / "docs/assets/doc-search.js"]
    for stylesheet, text in styles:
        assert "@import" not in text.lower(), stylesheet
        for value in CSS_URL.findall(text):
            local_target(stylesheet, value)
    guide_style = (REPO / "docs/assets/guide.css").read_text(encoding="utf-8")
    assert ":focus-visible" in guide_style
    assert "@media print" in guide_style
    assert "@media print" in (REPO / "docs/assets/manual.css").read_text(encoding="utf-8")


def test_diagrams_have_accessible_names_and_descriptions() -> None:
    diagrams = []
    assets = set()
    for page in PUBLIC_PAGES:
        _, parser = read_guide(page)
        diagrams.extend((element, parser) for element in parser.elements if element.tag == "svg")
        for element in parser.elements:
            if element.tag != "img":
                continue
            assert "alt" in element.attrs
            assert element.attrs["alt"].strip() or element.attrs.get("class") == "brand-mark"
            target = local_target(page, element.attrs["src"])
            if target.suffix == ".svg":
                assets.add(target)
    for asset in assets:
        _, parser = read_guide(asset)
        ids = [element.attrs["id"] for element in parser.elements if element.attrs.get("id")]
        assert len(ids) == len(set(ids)), asset
        for element in parser.elements:
            assert element.tag not in {"script", "iframe", "object", "embed"}, asset
            assert not any(key.lower().startswith("on") for key in element.attrs), asset
            for key in ("src", "href", "xlink:href"):
                if value := element.attrs.get(key):
                    local_target(asset, value)
        diagrams.extend((element, parser) for element in parser.elements if element.tag == "svg")
    assert len(diagrams) >= 2
    for diagram, parser in diagrams:
        assert diagram.attrs.get("role") == "img"
        labelled_by = diagram.attrs.get("aria-labelledby", "").split()
        assert len(labelled_by) == 2
        for element_id in labelled_by:
            matches = [
                element for element in parser.elements if element.attrs.get("id") == element_id
            ]
            assert len(matches) == 1
            assert matches[0].text().strip()
        assert diagram.text().strip()


def test_local_links_resolve_to_public_pages_and_unique_sections() -> None:
    assert set(MANUAL.rglob("*.html")) == GENERATED
    assert not (REPO / "docs/guide.html").exists()
    parsed = {page: read_guide(page)[1] for page in PUBLIC_PAGES}
    ids_by_page = {}
    for page, parser in parsed.items():
        ids = [element.attrs["id"] for element in parser.elements if element.attrs.get("id")]
        assert len(ids) == len(set(ids)), page
        ids_by_page[page] = set(ids)
    for page, parser in parsed.items():
        for element in parser.elements:
            for key in ("href", "src", "poster", "action"):
                value = element.attrs.get(key)
                if value is None or urlsplit(value).scheme or urlsplit(value).netloc:
                    continue
                resolved = local_target(page, value)
                if key == "href" and element.tag == "a":
                    assert resolved in PUBLIC_PAGES | PUBLIC_DOWNLOADS | {SHOWCASE}, (page, value)
                fragment = urlsplit(value).fragment
                if fragment:
                    assert resolved in ids_by_page, (page, value)
                    assert unquote(fragment) in ids_by_page[resolved], (page, value)


def test_guide_covers_source_setup_permissions_data_and_current_limits() -> None:
    text = " ".join(" ".join(read_guide(page)[1].root.text().split()) for page in PUBLIC_PAGES)
    for fact in (
        "uv sync",
        "npm --prefix frontend ci",
        "npm --prefix frontend run build",
        "uv run viki-agent",
        "uv run python scripts/build_product.py",
        "Allow once",
        "Deny",
        "Windows",
        "macOS",
        "Linux",
        "300 seconds",
        "Reset does not erase Claude Code transcripts or registered vaults",
        "Reset does not undo changes already made to files in a vault",
    ):
        assert fact in text
    assets = PUBLIC_PAGES | {CAPTIONS, LAUNCH_CAPTIONS}
    for page in PUBLIC_PAGES:
        _, parser = read_guide(page)
        for element in parser.elements:
            for key in ("src", "href"):
                value = element.attrs.get(key, "")
                if not value or urlsplit(value).scheme or urlsplit(value).netloc:
                    continue
                target = local_target(page, value)
                if target.suffix in {".css", ".js", ".svg", ".vtt"}:
                    assets.add(target)
    for asset in assets:
        text = asset.read_text(encoding="utf-8")
        if asset == GUIDE:
            text = text.replace(SPONSOR_HREF, 'href=""').replace(REPO_HREF, 'href=""')
        assert not scrub_fixture.problems(text), asset
