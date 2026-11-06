---
name: Fleet web pages
version: 1.2.0
description: >
  The fleet's design contract for web pages: public, static, content-first
  sites that a stranger reads once, mostly on a phone, served as a static
  folder from a CDN. The web-app sibling is `~/.claude/design.md` (with
  `design.dark.md`); nothing there applies to a web page and nothing here
  applies to a web app. This file fixes what every web page shares: token
  names and roles, floors, layout, component contracts, image rules,
  accessibility, trust cues, search facts and budgets. Each site sets its own
  brand values (colours, typeface, radius, hand-drawn accents) in its own
  stylesheet. The rendered rubric is `design.web.rubric.toml`.
viewports:                    # the verification matrix, CSS px
  phone:   390px              # 390x844 at scale 2, touch
  tablet:  800px              # the band where a fixed track squeezes text
  desktop: 1440px             # 1440x900
breakpoints:                  # mobile first; min-width queries only
  sm: 640px                   # two cards per carousel row
  md: 768px                   # header nav replaces the menu button; media beside text
  lg: 1024px                  # three cards per carousel row
type:
  body-min:      16px         # running text, card text, captions, form fields
  meta-min:      14px         # meta and field labels only
  label-min:     12px         # uppercase label role only
  body-leading:  1.5          # line height floor for running text
  measure-min:   45ch         # running text column, narrowest
  measure-max:   75ch         # running text column, widest
contrast:
  text:          4.5          # body and meta text on its background
  large-text:    3            # 24px+, or 18.66px+ at 700
  non-text:      3            # field borders, focus ring, icons that carry meaning
  band-text:     7            # body text on a dark band (the footer)
  band-label:    4.5          # label text on a dark band
targets:
  hit-min:       44px         # effective tap square for every link and control
  control:       48px         # button and field height
  menu-row:      56px         # phone menu rows
  spacing-min:   24px         # an undersized target keeps a 24px circle clear
focus:
  outline:       2px          # :focus-visible ring width, solid
  offset:        2px
space:
  base:          4px          # every spacing value is a multiple
images:
  social-card:   1200x630     # og:image / twitter:image, JPEG
  thumb-phone:   96px
  thumb-wide:    160px        # from lg
  feature-max:   520px
seo:
  title-min:        30        # characters
  title-max:        60
  description-min:  70
  description-max:  160
  h1-count:         1
performance:                  # cold first load, phone profile, throttled (Lighthouse mobile)
  lcp:           2.5s
  cls:           0.1
  inp:           200ms        # field; lab proxy is total blocking time
  tbt:           200ms
  lighthouse-min: 95          # performance, accessibility, best practices, SEO, each
  html-max:      50KB         # one page's HTML, transferred
  font-files-max: 4
  font-bytes-max: 150KB
  js-first-party-max: 50KB
budgets:                      # per page type: transferred bytes / requests, cold phone load
  landing:  { bytes: 750KB, requests: 30 }   # home, about, a service page
  listing:  { bytes: 750KB, requests: 30 }   # a gallery, a shop, an index of articles
  article:  { bytes: 500KB, requests: 20 }   # an article, an interview with a video
  product:  { bytes: 300KB, requests: 15 }   # one product
  utility:  { bytes: 250KB, requests: 15 }   # contact, legal, 404
---

# Web pages: the contract and the brand

A **web page** here is a public, static, content-first site: robertoferraro.net is the first. A **web app** is a FastAPI service with a static PWA front end, held to `design.md`. The two have different readers and different failures, so they have different standards.

| | Web app (`design.md`) | Web page (this file) |
| --- | --- | --- |
| Reader | the owner, signed in, every day | a stranger, once, mostly on a phone |
| Goal | get a task done | understand, trust, then act (contact, book, buy, subscribe) |
| Navigation | the floating bottom-tab pill | a top header: brand, text nav, a phone menu panel |
| Theme | light and dark | the site's brand, light by default; dark is optional, and its switch has one contract (section 4) |
| Identity | the fleet's shared look | the owner's own brand |
| Must not regress | behaviour | the search facts of every URL |
| Performance | warm, on the LAN | cold first load on a phone over mobile data |
| Hosting | a local process | a static folder on a CDN (Cloudflare Pages) |

The file has two halves. **The contract** (sections 1 to 12) is fleet-wide and holds no brand values. **The brand** (section 13) is what each site sets, with robertoferraro.net as the worked example. The rubric `design.web.rubric.toml` turns the contract into rules with a metric, a pass rule, a severity and a cited standard; section 14 says how it is read. Sources are listed in section 15 and cited inline as **Why** lines.

# Part one: the contract

## 1. Principles

1. **Content first.** Text and images are the content; the interface stays quiet around them. A page earns attention with hierarchy, not decoration.
2. **Phone first.** Design and check at 390 px before anything wider. Assume most readers are on a phone, on mobile data, arriving cold from a search result or a shared link.
3. **One left edge.** Text blocks start at the same left edge; nothing is centred or right-aligned except a single short line inside a component that is centred by contract (a play button). Running text stops at the measure.
4. **Size by role.** An image is a feature, a tile or a thumbnail, whatever column the author placed it in.
5. **One accent, one typeface family, one radius, one frame.** The brand picks each; the contract allows only one of each.
6. **Every URL is a search result.** Its title, description, canonical, social card, headings, body text, alt text and links are part of the design and must not regress.
7. **Fast on a cold phone.** Every page meets the Core Web Vitals and its page-type budget on a throttled mobile profile.

**Why:** strangers scan before they read, and decide fast whether a page is worth it ([NNg F-shaped pattern](https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content/), [NNg visual hierarchy](https://www.nngroup.com/articles/visual-hierarchy-ux-definition/)); mobile-first layout is a priority order, not a smaller copy of the desktop ([NNg mobile first](https://www.nngroup.com/articles/mobile-first-not-mobile-only/)).

## 2. Tokens: names and roles

A site's stylesheet declares these custom properties on `:root`. The names and roles are the contract; the values are the brand (section 13). A site may add tokens for its own components, but it never renames these or gives one of them a second role.

| Token | Role | Contract rule |
| --- | --- | --- |
| `--paper` | page and image background | text tokens meet their contrast floor against it |
| `--ink` | text, headings, primary button fill, focus ring | at least 4.5:1 on `--paper` |
| `--ink-2` | meta, captions, prices, labels | at least 4.5:1 on `--paper` |
| `--line` | hairline frames and rules | decorative only: never the only boundary of a control |
| `--line-strong` | form-field borders | at least 3:1 on `--paper` |
| `--accent` | the brand's one accent | never the only cue for meaning; as text it meets 4.5:1 |
| `--footer-band` | the footer background | white text on it 7:1, label text 4.5:1 |
| `--font` | the one typeface family, with a system fallback stack | see section 3 |
| `--fs-title`, `--fs-h2`, `--fs-h3`, `--fs-lead`, `--fs-body`, `--fs-card`, `--fs-small`, `--fs-label` | the type roles | see section 3 |
| `--s1` … `--s9` | the spacing scale | multiples of 4 px |
| `--gutter`, `--gap`, `--section` | page edge, block gap, space around a section rule | from the spacing scale or a `clamp()` between two of its steps |
| `--radius` | the one corner radius | every framed image, control and tile |
| `--frame` | the one hairline frame (`1px solid var(--line)`) | every content image and framed tile |
| `--control` | button and field height | at least 48 px |
| `--header` | sticky header height | at most 72 px, so a phone keeps its reading space |
| `--content` | widest content column | the `.wrap` container |
| `--measure` | widest running text | between 45 and 75 characters of body text |
| `--img-feature`, `--img-tile`, `--img-thumb` | the image role widths | see section 7 |

**Why:** a fixed vocabulary is what lets one static lint and one rendered review read every site; the values stay free so each owner keeps their identity.

## 3. Type

- **One family**, at most three faces (for example regular, italic, medium), each a latin subset in WOFF2. A second family is a brand change, not a component choice.
- **Roles, not sizes.** Every text run takes one of eight roles: title (the page's one `h1`), h2, h3 (subhead and card title), lead, body, card (card text, captions, lists), small (meta and field labels) and label (short uppercase column heads).
- **Floors.** Body, card text, captions and form fields are at least 16 px, so iOS does not zoom a focused field and a phone reads without pinching. Meta and field labels are at least 14 px. The label role is at least 12 px and is the only role set in capitals.
- **Leading.** Running text has a line height of at least 1.5; headings sit between 1.1 and 1.3.
- **Scale.** Fluid roles use `clamp(min, preferred, max)` with a `rem` floor and ceiling, so the text still scales with the reader's font-size setting.
- **Headings** are never italic and never smaller than the body text they introduce.

**Why:** [WCAG 2.2 1.4.4 Resize text](https://www.w3.org/TR/WCAG22/#resize-text) and [1.4.12 Text spacing](https://www.w3.org/TR/WCAG22/#text-spacing); [GOV.UK type scale](https://design-system.service.gov.uk/styles/type-scale/) keeps body at 19 px on large screens and 16 px on small; [web.dev font best practices](https://web.dev/articles/font-best-practices) for subsetting and WOFF2.

## 4. Space, shape and colour

- **Spacing** comes from a 4 px scale. Between blocks inside a section, `--gap`; between sections, `--section` above and below the section rule.
- **One radius and one frame.** Every content image, control and framed tile takes `--radius` and `--frame`. No shadows, no gradients.
- **Colour** comes from the brand tokens only. One accent; no second accent; status colours appear only on a form's own messages.
- **Light by default, dark optional.** A site may add a dark theme. If it does, it follows the dark-theme contract below and every contrast floor holds in both themes.
- **No literal colour in a component.** A component's stylesheet holds tokens, never a hex, `rgb()` or named colour; a colour lives once, as a token value in the site's stylesheet.

### Dark theme (optional)

The project-scaffolding `site-theme` component implements this; a site vendors it verbatim and sets only its dark values.

- **State.** The visitor's saved choice is `data-theme="light"` or `"dark"` on `<html>`, saved in `localStorage` under the key `theme`. Absent means follow the system setting. Nothing else stores a theme.
- **Tokens.** A token that differs between themes is written `light-dark(<light>, <dark>)`, which follows `color-scheme`. The component sets `color-scheme: light dark` on `:root`, narrowed to the chosen one by `data-theme`, so the system setting decides until the visitor presses the switch. There is no second stylesheet and no per-colour media query. Without JavaScript the system setting still applies.
- **Footer band.** The band is dark in both themes: `--footer-band` takes a dark value in each, and its text tokens `--band-ink`, `--band-label` and `--band-rule` keep one light value in both, never the page colour.
- **Drawings.** A token that is a data-URI drawing has its colour baked in and cannot use `light-dark()`. A site that wants it readable on dark points it at a dark version under `[data-theme="dark"]` and again inside `@media (prefers-color-scheme: dark)` on `:root:not([data-theme="light"])`.
- **Switch.** One `button.theme-toggle`, 44 px square, with `aria-pressed` (true while dark) and a fixed `aria-label` that names the dark theme, because a label that changes with the state is read twice. It shows a Lucide moon in light and a sun in dark.
- **No flash.** A blocking one-line `<script>` in `<head>`, before the stylesheets, applies a saved choice before the first paint; it is never `defer`, `async` or a module. The switch script is a deferred module that reads the state at load and writes `data-theme` only when the switch is pressed.
- **Storage blocked** (private mode, site data off): a press still switches the page, for that page only.
- **Floors.** Every contrast floor of section 8 and the front matter holds for the dark values as for the light ones.

**Why:** [WCAG 2.2 1.4.3 Contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum) applies to a page in whichever theme it renders; [MDN `light-dark()`](https://developer.mozilla.org/en-US/docs/Web/CSS/color_value/light-dark) and [`color-scheme`](https://developer.mozilla.org/en-US/docs/Web/CSS/color-scheme) are the mechanism; a saved choice applied after first paint flashes the other theme, which is why the boot line blocks.

## 5. Layout

- **The container.** `.wrap` is centred at `--content` with `--gutter` padding and is the size container (`container-type: inline-size`), so `100cqi` is the content width and image roles resolve against it.
- **Sections.** A page is a stack of sections on `--paper`, separated by space and the brand's section rule. Grey bands between sections are not used: they compete with images that carry their own backgrounds.
- **Phone (under 768 px).** Every section is one column, read in one order: **title, text, picture, then the action** (button or form). A block whose source order differs sets its phone order with an `order` variable; the rule is the layout's, not a per-page fix. A picture is never read before the body text that introduces it, unless the desktop also shows it first.
- **From 768 px.** A row of text beside a media column gives the media its role width on the side the author chose, and the text the rest. Text columns of one row share it equally, tops aligned.
- **The tablet band (around 800 px).** A track defined as `minmax(0, 520px) 1fr` grows to 520 px before the `1fr` track gets anything, which left a 152 px text column on the seed site. Image roles are therefore written as `min(520px, calc((100cqi - var(--gap)) / 2))`, so a picture and its text share the row evenly.
- **Running text** stops at `--measure`, also inside a wide section.
- **No horizontal page scroll** at any viewport from 320 px up.

**Why:** line length between 50 and 75 characters reads best ([Baymard line length](https://baymard.com/blog/line-length-readability), [WCAG 2.2 1.4.8 Visual presentation](https://www.w3.org/WAI/WCAG22/Understanding/visual-presentation)); [GOV.UK paragraphs](https://design-system.service.gov.uk/styles/paragraphs/) left-aligns body text; [WCAG 2.2 1.4.10 Reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow) sets the 320 px single-direction scroll.

## 6. Components

Each contract is the shape and behaviour every site shares. The project-scaffolding site components implement them; a site skins them with its brand tokens.

- **Header.** Sticky, on `--paper`, `--header` tall. The brand (logo or name) on the left links home; the text nav on the right; the current page is marked by more than colour (the brand's marker, an underline, or weight) and carries `aria-current="page"`. A hairline appears once the page scrolls. Never put `backdrop-filter`, `filter` or `transform` on the header: each makes it the containing block for `position: fixed` children, and the phone menu panel shrinks to the header's height.
- **Phone menu.** Under 768 px a menu button (44 px, labelled, `aria-expanded`, `aria-controls`) opens a full-width panel under the header with rows at least 56 px tall. The panel traps nothing: Escape and the button close it, and focus returns to the button. Tested by hit test: the centre of each link, through `elementFromPoint`, returns that link.
- **Section rule.** The brand's divider between sections (a hand-drawn SVG, a hairline). Decorative: `aria-hidden` or a CSS background, never a content image.
- **Card.** A framed square picture with the words underneath on the paper, with no box around the whole card. The title (h3 role) sits 12 px under the picture. The card's first link is stretched over the whole card; hovering darkens the frame. Drawings sit whole (`object-fit: contain`); photos fill (`cover`). On a phone a list of cards becomes rows: thumbnail left, words right. Card texts in one set are of one length (for example 35 to 45 words), so a row ends level without clamping.
- **Carousel.** Whole cards only: three from 1024 px, two from 640 px, and on a phone one card at about 82% with the next card's edge showing. Built with `grid-auto-columns: calc((100% - (n - 1) * var(--gap)) / n)` and scroll snap, not fixed card widths. Arrows (44 px, labelled) sit centred under the row and are hidden when nothing scrolls; one click moves one card plus the gap, instantly under reduced motion. No autoplay, no dots, no carousel of single images.
- **Testimonial.** A framed quote tile with the brand's quote mark, the name below as a label. On a desktop a set shows as a grid with nothing to scroll; on a phone or tablet it may be a carousel.
- **Product page.** From 768 px the picture sits on the left at the feature width, sticky, centred in the space under the header and never more than 160 px below it, so no blank stretch taller than 200 px opens beside the description. On the right at the measure: title, price at the lead size, the buy button directly under it, then the description.
- **Video.** A re-hosted thumbnail with a 64 px labelled play button; the player (YouTube on its `nocookie` host, or another) loads only on tap. 16:9, `--radius`.
- **Buttons.** `--control` tall, `--radius`, a verb label at the body size. Primary: `--ink` fill with `--paper` text. Secondary: `--paper` with an `--ink` border. A disabled button changes its border style, never only its opacity.
- **Fields.** `--control` tall, `--line-strong` border, 16 px text, a visible label above (never a placeholder as the label), errors in text beside the field.
- **Newsletter row.** Field and button share height, radius, text size and top edge (`minmax(0, 1fr) auto`). It posts to the site's own endpoint.
- **Accordion.** Native `<details>` rows at least 56 px tall between hairlines.
- **Footer.** A `--footer-band` band: white text at 7:1 or better, label heads at 4.5:1 or better, link rows at least 44 px tall, on a phone two link columns. It carries the contact route, the social links (44 px framed squares) and the legal links.

**Why:** [WCAG 2.2 2.5.8 Target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum); [NNg carousels](https://www.nngroup.com/articles/designing-effective-carousels/) and [NNg auto-forwarding](https://www.nngroup.com/articles/auto-forwarding/) against autoplay and for visible next-item cues; [web.dev third-party facades](https://developer.chrome.com/docs/lighthouse/performance/third-party-facades) for tap-to-load video; [Baymard product page research](https://baymard.com/research/product-page) for price and buy beside the image.

## 7. Images

- **Roles.** Feature: column width on a phone, `min(--feature-max, half the row)` from 768 px, used for the first section's image, a lone image and a video beside text. Tile: column width on a phone, half the content from 768 px, a third from 1024 px. Thumb: 96 px, 160 px from 1024 px. Images that share a role render at equal width (±2 px).
- **Whole, at their own proportions.** A content image is never cropped except a photo filling a square card. Portraits are square sources rounded by CSS; a circle baked into a square file is not used. Portraits in one list share one size and radius, each with its top level with the name beside it (±4 px).
- **Frame.** Every content image takes `--frame`, `--radius` and a `--paper` background; a cut-out with transparent corners keeps its own shape, unframed.
- **Responsive variants.** Every content image ships WebP (or AVIF) variants at several widths with `srcset` and a `sizes` value per role, and explicit `width` and `height` attributes so nothing shifts as it loads.
- **Loading.** The largest above-the-fold image (the LCP candidate) is eager, with `fetchpriority="high"`; everything below the fold takes `loading="lazy"` and `decoding="async"`.
- **Alt text.** Content images carry alt text that says what the image shows in context; decorative images carry `alt=""`.
- **Social card.** Every indexable page names a 1200x630 JPEG for `og:image` and `twitter:image`.

**Why:** [web.dev responsive images](https://web.dev/articles/serve-responsive-images), [web.dev lazy loading](https://web.dev/articles/browser-level-image-lazy-loading), [web.dev optimize LCP](https://web.dev/articles/optimize-lcp) and [optimize CLS](https://web.dev/articles/optimize-cls); [Google image SEO](https://developers.google.com/search/docs/appearance/google-images) for alt text and descriptive file names.

## 8. Accessibility floor (WCAG 2.2 AA)

- Contrast: text 4.5:1, large text 3:1, field borders and meaningful icons 3:1, as in the front matter.
- Every interactive element is reachable and operable by keyboard, in reading order, with a visible `:focus-visible` ring (2 px solid, 2 px offset, at least 3:1 against what surrounds it) that a sticky header never hides.
- Effective tap targets are at least 44 px square, or keep a 24 px circle clear of every neighbour.
- `<html lang>` is set; landmarks (`header`, `nav`, `main`, `footer`) are present; a skip link reaches `main`.
- Headings form an ordered outline: one `h1`, no skipped level going down.
- Motion respects `prefers-reduced-motion`; nothing moves on scroll, and nothing autoplays.
- Contact and help sit in the same place on every page (the header or the footer).

**Why:** [WCAG 2.2](https://www.w3.org/TR/WCAG22/): [1.4.3 Contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum), [2.4.7 Focus visible](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible), [2.4.11 Focus not obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum), [2.5.8 Target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum), [3.2.6 Consistent help](https://www.w3.org/WAI/WCAG22/Understanding/consistent-help), [2.3.3 Animation from interactions](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions); [GOV.UK focus states](https://design-system.service.gov.uk/get-started/focus-states/).

## 9. Hierarchy, trust and conversion

- **One job per page.** Each page has one primary action (contact, book, buy, subscribe), drawn as the one primary button in its section; secondary actions are secondary buttons or links.
- **The first screen** on a phone says who this is and what they offer: the title, a lead line and either the primary action or the start of the content.
- **Trust cues.** A real name and face or organisation, a working contact route reachable in one tap from every page, social proof (testimonials, clients, press) with names, and clear prices where something is sold.
- **Link text** says where it goes; "click here" and a bare "read more" are not used.
- **Copy** is the owner's: concise, scannable, front-loaded headings and lead lines.

**Why:** [NNg trustworthy design](https://www.nngroup.com/articles/trustworthy-design/) (design quality, up-front disclosure, comprehensive and current content, connection to the rest of the web); [NNg heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/) for recognition and consistency; [Google crawlable links](https://developers.google.com/search/docs/crawling-indexing/links-crawlable) for descriptive anchor text.

## 10. Search facts per URL

Every indexable URL has:

- a unique `<title>` of 30 to 60 characters, naming the page first and the site after;
- a unique `<meta name="description">` of 70 to 160 characters;
- an absolute, self-referencing `<link rel="canonical">`;
- Open Graph tags (`og:title`, `og:description`, `og:url`, `og:type`, `og:image` at 1200x630 as an absolute URL) and `twitter:card` set to `summary_large_image`;
- exactly one `h1` and an ordered heading outline;
- alt text on every content image;
- descriptive internal link text and no broken internal links;
- structured data as JSON-LD where it applies, validated: `Person` or `Organization` on the home page, `Product` on a product page, `Article` or `VideoObject` on an article or interview;
- `<html lang>`;
- no `noindex`, in a meta tag or an `X-Robots-Tag` header, unless the page is meant to stay out of search.

Every site has:

- `sitemap.xml` listing every indexable canonical URL and nothing else;
- `robots.txt` that allows crawling and names the sitemap;
- a 301 for every moved URL, in one hop (`_redirects` on Cloudflare Pages);
- a 404 page served with a 404 status;
- HTTPS only.

**Why:** Google Search Central on [title links](https://developers.google.com/search/docs/appearance/title-link), [snippets](https://developers.google.com/search/docs/appearance/snippet), [canonical URLs](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls), [structured data](https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data) (with the [Rich Results Test](https://search.google.com/test/rich-results)), [sitemaps](https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview), [robots.txt](https://developers.google.com/search/docs/crawling-indexing/robots/intro), [redirects](https://developers.google.com/search/docs/crawling-indexing/301-redirects) and [noindex](https://developers.google.com/search/docs/crawling-indexing/block-indexing); [the Open Graph protocol](https://ogp.me/); [X cards](https://developer.x.com/en/docs/x-for-websites/cards/overview/abouts-cards); [MDN lang](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Global_attributes/lang). Google publishes no fixed title or description length; the ranges here keep a title from truncating in a phone result and a description from being replaced for being too short.

## 11. Performance budget

Measured on a cold first load with a throttled mobile profile (Lighthouse's mobile emulation), never over the LAN:

- **Core Web Vitals:** LCP ≤ 2.5 s, CLS ≤ 0.1, INP ≤ 200 ms (in the lab, total blocking time ≤ 200 ms stands in for INP).
- **Lighthouse floor:** performance, accessibility, best practices and SEO each ≥ 95 on mobile.
- **Page weight and requests** by page type, from the front matter's `budgets`. HTML at most 50 KB per page; at most 4 font files and 150 KB of fonts; at most 50 KB of first-party JavaScript.
- **Fonts:** subset WOFF2, `font-display: swap`, the faces used above the fold preloaded.
- **Third parties:** none render-blocking. Embeds load on interaction (video) or lazily; a form posts to the site's own endpoint (for example Cloudflare Pages Functions), not a vendor iframe; statistics are the site's own and load only after an opt-in consent.
- **Delivery:** Brotli or gzip on text; long-lived immutable caching for hashed assets and revalidation for HTML, set in `_headers` on Cloudflare Pages.

The seed site's v3 rebuild meets every page-type budget with room: its heaviest landing page was 664 KB in 29 requests, its product page 94 KB in 7, against 1.5 to 4.8 MB on the hosted builder it replaced.

**Why:** [web.dev Web Vitals](https://web.dev/articles/vitals) and its [LCP](https://web.dev/articles/lcp), [INP](https://web.dev/articles/inp) and [CLS](https://web.dev/articles/cls) thresholds; [Lighthouse](https://developer.chrome.com/docs/lighthouse/overview) and its [performance scoring](https://developer.chrome.com/docs/lighthouse/performance/performance-scoring); [web.dev performance budgets](https://web.dev/articles/performance-budgets-101); [render-blocking resources](https://developer.chrome.com/docs/lighthouse/performance/render-blocking-resources); [third-party JavaScript](https://web.dev/articles/optimizing-content-efficiency-loading-third-party-javascript); [text compression](https://developer.chrome.com/docs/lighthouse/performance/uses-text-compression); [HTTP cache](https://web.dev/articles/http-cache); [Cloudflare Pages headers](https://developers.cloudflare.com/pages/configuration/headers/) and [redirects](https://developers.cloudflare.com/pages/configuration/redirects/).

## 12. Not used

The PWA navigation pill, modals, app icons and the web-app tokens of `design.md`. Shadows, gradients, grey section bands, a second accent, a second typeface family, boxes around whole cards, italic headings, centred text blocks, scroll animations, autoplay, carousels of single images, baked circular portraits, vendor form iframes and analytics that load before consent.

# Part two: the brand

## 13. What a site sets

A site sets the **values** of the section 2 tokens in its own stylesheet's `:root`, plus:

- its typeface files (within section 3's limits) and fallback stack;
- the exact `clamp()` of each type role, within the floors;
- its radius, header height, content width and measure (within the contract's ranges);
- its accent's uses (which words carry it, whether it is ever text);
- its hand-drawn or graphic accents (the section rule, a title marker, a quote mark), as SVG tokens, never as content images;
- optionally a dark theme (section 4): its dark values as `light-dark()` tokens, holding every floor.

The contract never fixes an owner's colours or typeface. A brand change is a change to the site's stylesheet; a contract change is a change to this file.

### Example brand: robertoferraro.net

The seed site, kept here as the worked example of section 13. Its own `docs/design.md` is the brand's source of truth; these values are illustrative, not fleet values.

| Token | Value | Note |
| --- | --- | --- |
| `--paper` | `#ffffff` | white paper |
| `--ink` | `#111111` | 18.9:1 |
| `--ink-2` | `#595959` | 7.0:1 |
| `--line` | `#e3e3e3` | hairline |
| `--line-strong` | `#8c8c8c` | 3.4:1, field borders |
| `--accent` | `#ffcc00` | the pen: never text, a border, a button or a fill |
| `--footer-band` | `#595959` | white on it 7.0:1; labels `#ededed` 6.0:1 |
| `--font` | Poppins, then the system stack | 400, 400 italic and 500; latin subsets, two preloaded |
| `--fs-title` … `--fs-label` | 32–48, 24–32, 18, 18–20, 16–17, 16, 14, 13 px | |
| `--radius` | 12 px | matches the rounded panels inside the drawings |
| `--control` | 48 px | |
| `--header` | 64 px, 72 px from 1024 px | |
| `--content` | 1120 px | |
| `--measure` | 40rem | |

Its signature is **the pen**: a yellow marker behind one or two words of each page title and the current nav item, a hand-drawn ink rule between sections, a drawn quote mark on testimonials, and an underline for one phrase per section. Drawings exported on grey paper are put on white at build time by a pixel swap that keeps their inner white panels visible.

# Verification and sources

## 14. How a page is checked

- **The rubric** `design.web.rubric.toml` lists each contract rule as data: a stable id, the metric it reads, the pass rule, a severity (P0 to P3), the standard it cites, a fix template and an owner (`spec`, `scaffold` or `app`). It shares the schema of `design.rubric.toml`, so the rendered review scores it the same way; a rule whose metric is not measured yet reports `unmeasured`, never a pass.
- **The matrix** is the front matter's `viewports`: 390 px phone, 800 px tablet and 1440 px desktop, full page, scrolled through once so lazy images load. The review starts the site's own static preview on a free loopback port, measures against it and stops it (`design_review measure-web`).
- **Performance and search facts** are read per URL from a cold phone-profile load and from the built HTML; Core Web Vitals come from a throttled mobile profile, never the LAN.
- **Theme.** The rendered review measures the light theme only, so the dark values are held to the same floors but not yet measured. The rubric has no rule for a literal colour in a component: a rendered page reports the resolved colour, not whether it came from a token, so only a read of the stylesheet could tell.
- **The brand** is never graded: a rule reads a token's role and floor, not its value.

## 15. Sources

- **Accessibility:** [WCAG 2.2](https://www.w3.org/TR/WCAG22/) and its Understanding documents linked above; [GOV.UK Design System](https://design-system.service.gov.uk/get-started/focus-states/).
- **Reading and hierarchy:** Nielsen Norman Group on [scanning](https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content/), [visual hierarchy](https://www.nngroup.com/articles/visual-hierarchy-ux-definition/), [mobile first](https://www.nngroup.com/articles/mobile-first-not-mobile-only/), [carousels](https://www.nngroup.com/articles/designing-effective-carousels/), [trust](https://www.nngroup.com/articles/trustworthy-design/) and [heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/); Baymard on [line length](https://baymard.com/blog/line-length-readability) and [product pages](https://baymard.com/research/product-page); [GOV.UK type scale](https://design-system.service.gov.uk/styles/type-scale/).
- **Search:** [Google Search Central](https://developers.google.com/search/docs/appearance/title-link) pages linked in section 10; [Open Graph](https://ogp.me/); [X cards](https://developer.x.com/en/docs/x-for-websites/cards/overview/abouts-cards).
- **Performance:** [web.dev](https://web.dev/articles/vitals) and [Lighthouse](https://developer.chrome.com/docs/lighthouse/overview) pages linked in section 11; [Cloudflare Pages](https://developers.cloudflare.com/pages/configuration/headers/).
