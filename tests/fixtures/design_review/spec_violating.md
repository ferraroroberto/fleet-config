---
name: Fixture
version: test
colors:
  canvas:        "#ffffff"
  card:          "#ffffff"
  fg:            "#1f2328"
  accent:        "#0969da"
  accent-fg:     "#ffffff"
  accent-soft:   "color-mix(in srgb, var(--accent) 16%, transparent)"
components:
  button-primary: { backgroundColor: "{colors.accent}", textColor: "{colors.accent-fg}", height: 40px }
  button-tint:    { backgroundColor: "{colors.accent-soft}", textColor: "{colors.accent}" }
  hit-target:     { min: 48px }
icons:
  size:
    inline:  16px
    feature: 24px
---
