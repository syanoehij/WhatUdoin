# Frontend Changes

- Changed `templates/base.html` so the current-user payload is rendered into an `application/json` script tag.
- Changed the JavaScript assignment to parse that JSON through `JSON.parse(...)`, leaving the executable script body as valid JavaScript before Jinja rendering.
- Replaced a direct `{{ user.name }}` JavaScript string comparison in the links dropdown with `window.CURRENT_USER.name`.
- Added accessible names for the reported dark-theme, all-day, start-time, end-time, priority, and bind-check filter controls.
- Added accessible names for the hidden kanban checkbox, recurrence end date input, and shared dialog prompt input after Edge Tools surfaced those remaining form controls.
- Removed static inline `style="..."` attributes from `templates/base.html` and moved those styles into `static/css/style.css`.
- Updated `static/js/event-modal.js` and `static/js/wu-dialog.js` to use `.hidden` class toggles for elements whose initial hidden state moved out of inline styles.

This addresses the VSCode JavaScript parser diagnostics, Edge Tools form-label errors, and the static inline-style warnings while preserving the rendered runtime behavior.
