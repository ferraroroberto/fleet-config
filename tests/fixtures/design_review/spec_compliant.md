---
name: Fixture
version: test
colors:
  canvas:        "#ffffff"
  card:          "#ffffff"
  fg:            "#1f2328"
  accent:        "#0550ae"
  accent-fg:     "#ffffff"
  accent-soft:   "color-mix(in srgb, var(--accent) 16%, transparent)"
components:
  button-primary: { backgroundColor: "{colors.accent}", textColor: "{colors.accent-fg}", height: 48px }
  card:           { backgroundColor: "{colors.card}", textColor: "{colors.fg}" }
  hit-target:     { min: 44px }
icons:
  size:
    inline:  16px
    title:   18px
    feature: 24px
---
