## Context

`report.html` already draws matplotlib charts, inlined as SVG and recolored to CSS variables so they follow the page theme. None of that reaches GitHub: markdown can't inline `<svg>`, can't run JS, and an `<img>` can't see the page's CSS variables. The only visuals GitHub renders from a committed markdown file are images from the repo and mermaid blocks.

## Goals / Non-Goals

**Goals:** charts that read on GitHub's light and dark themes; byte-identical files on re-run so commits only change when data does; one chart implementation for markdown and HTML; never crash or draw an empty chart for a thin or partial round.

**Non-Goals:** interactive charts on GitHub; PNG output; charts for every table column.

## Decisions

### Light/dark: one transparent SVG with a two-surface palette

Options considered:

1. **Two files per chart and `<picture><source media="(prefers-color-scheme: dark)">`.** GitHub documents it and follows its own theme setting. But it only works where the renderer allows raw HTML, doubles the committed files, and every other viewer (VS Code preview, other forges, local markdown tools) falls back to the light file.
2. **`#gh-dark-mode-only` / `#gh-light-mode-only` fragments.** GitHub-specific, older, and every non-GitHub renderer shows both images.
3. **`@media (prefers-color-scheme: dark)` inside the SVG.** Tracks the OS setting, not GitHub's theme, so a user who forces GitHub dark on a light OS gets dark-on-dark text.
4. **One SVG, transparent background, colors chosen to read on both surfaces.**

We use 4. It's one file, a plain `![alt](charts/x.svg)` link, and it looks the same in every renderer. The cost is contrast headroom, so the palette is picked for it and checked:

- Series use the dataviz reference palette's **dark-mode steps** (blue `#3987e5`, orange `#d95926`, aqua `#199e70`). They sit in the middle of the lightness band; the validator passes all checks, all pairs, against both `#ffffff` and `#0d1117` (CVD ΔE 9.4, normal-vision 20.9, every slot ≥ 3:1).
- The baseline and "other" marks are the reference muted grey `#898781` (3.6:1 on white, 5.3:1 on `#0d1117`).
- Text is a neutral `#777777`/`#767676` (4.5:1 on white, 4.2:1 on `#0d1117`). No grey clears 4.5:1 on both, this is the closest; chart text is secondary to the numbers in the tables next to it.
- Grid and axis lines are a grey with opacity (0.3 and 0.6), so they come out one step off the surface on either background instead of being a fixed light or dark hex.
- Dots have no surface-colored ring, since the surface is unknown; they're drawn above whiskers and lines instead.

`report.html` inlines the same SVG text and rewrites those hexes (and the translucent grid/axis) to its CSS variables, so the page still gets proper per-theme ink.

### No dual axis for tokens and cost

"Tokens as bars with cost as a secondary marker" would put two scales on one plot. `cost-tokens.svg` is small multiples instead: one row per arena, tokens bars on the left, cost dots with CI on the right, contenders sharing the y axis.

### Determinism

`svg.hashsalt` is fixed, `metadata={"Date": None, "Creator": None}`, the `<metadata>` block is dropped, fonts are the bundled DejaVu Sans for layout (`svg.fonttype: none`, so text stays text and the stack is rewritten to a system sans), and element ids get a per-chart prefix derived from the file name. Bootstraps use the report seed. Two runs on the same data give identical bytes; a test checks it.

### Data per chart

Arena charts pool each contender (and model, when the round has several) over tasks in that arena. The quality metric per arena is TP when it has ground truth, else judge-valid, else the chart is skipped. The findings breakdown takes `final_*` verdicts when present, else ground-truth `tp/fp/dup/unknown`, else judge `valid/invalid/unverifiable`; findings no column accounts for are shown as "other". Resources pool over arenas (they describe the harness). Anything with no finite value is skipped, n = 1 draws a point without an interval, and the markdown only links files that were written. Stale `*.svg` in `charts/` are removed first.

### Mermaid

Two flowcharts, `flowchart LR`, generated with opaque ids (`n0`, `n1`, …) so no id is a mermaid keyword or starts an `o`/`x` edge, and every label quoted with `"` escaped as `#quot;`. Round at a glance: planned bouts (from the lock's matrix, falling back to rows seen) → one node per status that occurred (ok always shown, plus "not run" when rows are missing) → findings in ok bouts → verdict totals. Pipelines: per pipeline contender, each stage's summed findings from the bout records, falling back to `findings_before_verify` → `findings`. `xychart-beta` is skipped: it's still beta and we couldn't confirm GitHub's renderer handles it, and the SVG charts already cover bars.

### `--no-charts`

Skips writing `charts/` and the markdown sections that reference it. `report.html` still inlines its charts (it's self-contained and never committed as images).

## Risks / Trade-offs

- Text at ~4.2–4.5:1 is below AA on one side. Mitigated by 9pt+ text, direct labels only where they help, and every value being in the tables.
- matplotlib version changes can change bytes. That's fine: the trials repo pins the engine version, and the determinism guarantee is for the same engine and data.
