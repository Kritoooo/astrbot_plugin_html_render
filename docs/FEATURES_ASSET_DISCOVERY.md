# Asset discovery helper

This repo currently has local runtime changes, so this feature pass avoids touching the already-dirty renderer path.
Instead, it adds a safe utility that helps users discover available template/background-like assets before wiring new config values.

## What it does

`tools/asset_inventory.py` scans the plugin checkout and emits a JSON inventory of:

- `templates`: HTML/CSS/Jinja-like files under template/layout/theme-style paths
- `backgrounds`: image files under background/image/assets-style paths
- `images`: other image assets
- `markup`: other markup/style assets

## Usage

From the plugin root:

```bash
python tools/asset_inventory.py --pretty
```

Write an inventory file for later inspection:

```bash
python tools/asset_inventory.py --pretty --write docs/asset_inventory.sample.json
```

Scan a different checkout explicitly:

```bash
python tools/asset_inventory.py /path/to/astrbot_plugin_html_render --pretty
```

## Why this is useful

When adding new options like default template names, background presets, or fallback asset selection, users need to know which files actually exist.
This helper provides that discovery step without changing plugin behavior.

## Compatibility

- No runtime behavior changes
- No dependency changes
- Safe to run on dirty trees
- Future config/schema work can reference generated inventory output
