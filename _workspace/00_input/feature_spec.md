# base.html VSCode / Edge Tools Diagnostic Check

## Request

VSCode and Microsoft Edge Tools report diagnostics in `templates/base.html`:

- JavaScript parser errors around the Jinja-rendered `CURRENT_USER` assignment.
- Axe form label errors for several controls.
- Webhint inline-style warnings throughout the shared modal markup.

## Scope

- Inspect reported lines and nearby script/form/modal context.
- Keep rendered behavior intact while removing editor-facing diagnostics where practical.
- Convert inline modal styles to CSS classes.
- Keep visibility toggles compatible with the existing modal JavaScript.

## Success Criteria

- `base.html` no longer embeds a raw Jinja object expression directly inside JavaScript source.
- Current-user data still becomes available as `window.CURRENT_USER`.
- Reported checkbox/time/select controls have accessible names.
- `base.html` has no static `style="..."` attributes.
- Template, Python import, and touched JavaScript syntax checks pass.
