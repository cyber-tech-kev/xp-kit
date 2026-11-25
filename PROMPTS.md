# Prompts: design your XP system with any LLM

These prompts work with ChatGPT, Claude, Gemini, Copilot, Llama, or whatever you use. Each one includes
everything the model needs to know, so it doesn't need access to this repo.

**The loop:**
1. Fill in the `[brackets]`, paste the prompt into your LLM.
2. Paste the result into the right file.
3. Run `python scripts/xp.py validate` to catch mistakes, and `python scripts/xp.py preview` to see the badges
   (open `_preview/index.html`).
4. If something's off, paste the error message back into the LLM and ask it to fix it.

You don't have to trust the model. The build checks every config and **rebuilds every icon from an allowlist**,
so a bad or malicious SVG gets rejected with a clear reason instead of ending up on your site.

> **Using an AI coding agent** (Claude Code, Cursor, Copilot agent mode)? Skip the copy-paste:
> open this repo and say *"Read PROMPTS.md and xp-config.yml, then design an XP system for [your group].
> Run `python scripts/xp.py validate` and `preview` until it passes."*

---

## 1. Design your whole XP system

Paste this to get a complete `xp-config.yml`.

````text
You are designing a gamified XP system for my group. Output ONE complete YAML file and nothing else.

ABOUT MY GROUP
- Name: [e.g. Lakeside Robotics Club]
- What we do: [e.g. build robots, compete in VEX, run workshops for middle schoolers]
- Size and rhythm: [e.g. ~25 members, meet weekly on Thursdays]
- Behaviors I want to reward most: [e.g. showing up, mentoring new members, finishing builds, competing]
- Vibe/aesthetic: [e.g. industrial, neon, cozy, minimal, retro arcade]
- Brand colors (optional): [e.g. #F26B1D orange and #1B1B1F charcoal]
- Season/semester dates (optional): [e.g. Spring 2027, Jan 15 to May 15]

RULES FOR THE YAML (follow exactly)
- Top-level keys: group, officers, theme, seasons, max_award, sources, levels, badges.
- group: name, handle (lowercase snake_case, max 24 chars), tagline (one short lowercase sentence),
  owner: "" , repo: xp, site_url: "", home_url: "".
- officers: [] (I'll fill this in).
- theme: accent, bg, panel, line, text, muted, badge_bg are hex colors like "#5DCAA5" (always quoted).
  Dark backgrounds look best (bg darkest, panel slightly lighter, line lighter still). shape is one of: shield, hex, coin.
- seasons: list of { name: "...", start: YYYY-MM-DD, end: YYYY-MM-DD }, or [] for none.
- max_award: 500
- sources: map of snake_case name -> { xp: <whole number or null>, unique: <true/false>, label: "<past-tense phrase>" }.
  Must include `attendance` (unique: true), `award` (xp: null, unique: false) and `correction` (xp: null, unique: false).
  Use unique: true for anything tied to a specific event, so it can't be double counted.
  Point values: small for routine things (5-15), bigger for effort (25-75). 4-8 sources total.
- levels: 4-6 levels, lowest first, as { name: snake_case, min: <whole number>, color: <color> }.
  First min is 0. Each min roughly 2x the previous (e.g. 0, 100, 300, 700, 1500). Names should fit the group's theme.
- badges: 4-8 badges, map of snake_case name (max 32 chars) -> {
    icon: one of drop, prompt, flag, bars, crosshair, nodes, star, bolt, book, code, heart, trophy, wrench, mic, chat, rocket
          (or a new snake_case name if I'll draw a custom icon for it; list those names at the very end as a YAML comment),
    color: <color>,
    shape: optional, one of shield, hex, coin,
    metric: count | streak | joined_before,
    sources: [list of source names]   (only for count),
    date: YYYY-MM-DD                   (only for joined_before),
    tiers: [one number] for a one-time badge, or [bronze, silver, gold] as 3 increasing numbers,
    desc: "<short description>" }
  `streak` means consecutive meetings attended. Include one joined_before "founding member" badge dated about a month out.
- Colors are either a name (red orange amber yellow green teal blue purple pink gray) or a quoted hex like "#3FA9F5".
- Put a space after every colon. Use inline maps { like: this } for sources, levels and badges. No tabs.
- Every source a badge counts must exist under sources.

Make the names and labels feel like they belong to my group, not generic.
````

## 2. Draw a custom badge icon

Badges come with 16 built-in icons. For anything else, have an LLM draw one and save it as `icons/<name>.svg`.

````text
Draw a simple, bold icon of [a wrench crossed with a lightning bolt] as an SVG for a small badge.

STRICT RULES (the file is machine-checked and will be rejected otherwise):
- Root element: <svg xmlns="http://www.w3.org/2000/svg" viewBox="-16 -16 32 32">. The center is 0,0.
  Keep the drawing inside roughly -14..14 on both axes.
- Only these elements: path, circle, ellipse, rect, line, polyline, polygon, g.
- Colors: only fill="currentColor", fill="none" or stroke="currentColor". No hex colors, no gradients.
- For a "glowing outline" look: stroke="currentColor" stroke-width="2.2" stroke-linecap="round"
  stroke-linejoin="round", and fill="currentColor" fill-opacity="0.25" on the main shape.
- Allowed attributes: d, cx, cy, r, rx, ry, x, y, width, height, x1, y1, x2, y2, points, fill, stroke,
  stroke-width, stroke-linecap, stroke-linejoin, stroke-opacity, fill-opacity, opacity, fill-rule,
  stroke-dasharray, and transform using only translate(), scale() or rotate().
- NO text, style, class, id, defs, filters, images, links, scripts, or animation.
- Keep it to under 15 elements. It renders at about 45px, so favor a few strong shapes over detail.

Output only the SVG.
````

Then check and preview it:

```bash
python scripts/xp.py check-icon icons/wrench_bolt.svg
python scripts/xp.py preview
```

**Prefer real icon sets?** Square, `currentColor` icons from sets like [Lucide](https://lucide.dev) or
[Tabler](https://tabler.io/icons) usually work as-is: download the SVG into `icons/` and run `check-icon`.
Keep their license notice (both are permissive and ask for attribution).

## 3. Turn your brand colors into a theme

````text
Make a dark-mode color theme for a web leaderboard from these brand colors: [#F26B1D, #1B1B1F].
Output only this YAML block with quoted hex values:

theme:
  accent: "#......"    # brand color used for links, buttons and highlights; must be readable on bg
  bg: "#......"        # page background, very dark
  panel: "#......"     # cards, slightly lighter than bg
  line: "#......"      # borders, lighter than panel
  text: "#......"      # main text, light
  muted: "#......"     # secondary text, mid-gray tinted toward the brand
  badge_bg: "#......"  # inside of badges, usually same as panel
  shape: [shield | hex | coin]

Text and accent must have a contrast ratio of at least 4.5:1 against bg.
````

## 4. Add one more badge

````text
Here is my xp-config.yml:
[paste it]

Add one new badge that rewards [members who bring a friend to a meeting]. If it needs a new source, add that too.
Follow the same format as the existing badges and sources exactly. Output the full updated YAML.
````

## 5. Announce it to your members

````text
Write a short, hype announcement for [Discord / Slack / Instagram / email] launching an XP and badge system for
[group name]. Members earn XP for [list from your sources], level up through [list your level names], and unlock
badges they can show on their GitHub profile. Joining takes about two minutes with a free GitHub account.
Join link: [https://github.com/OWNER/REPO/issues/new?template=join-xp.yml]
Leaderboard: [https://OWNER.github.io/REPO]
[Optional: anyone who joins before DATE gets the founding member badge forever.]
Tone: [e.g. casual and fun, under 120 words].
````

---

## When the LLM gets it wrong

Paste the error straight back. These are the usual ones:

| Error | Tell the LLM |
|---|---|
| `isn't valid YAML at line N` | "Fix the YAML syntax at line N. Put a space after every colon and don't use tabs." |
| `counts source X which isn't defined` | "Badge Y counts source X, but X isn't under sources. Add it or change the badge." |
| `fill="#ff0000" ... use currentColor` | "Replace every color with currentColor or none." |
| `<text> isn't allowed` | "Remove all text; draw the idea with shapes only." |
| `the viewBox must be (roughly) square` | "Use viewBox="-16 -16 32 32"." |
