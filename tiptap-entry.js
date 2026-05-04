// Tiptap bundle entry point — all extensions re-exported as named exports
// This file is compiled by rollup into static/lib/tiptap-bundle.min.js
// Global name: TiptapBundle

export { Editor, Extension, InputRule, Mark, getHTMLFromFragment } from '@tiptap/core';
export { Plugin, PluginKey } from '@tiptap/pm/state';
export { Decoration, DecorationSet } from '@tiptap/pm/view';
export { Fragment } from '@tiptap/pm/model';
export { StarterKit } from '@tiptap/starter-kit';
export { Paragraph } from '@tiptap/extension-paragraph';
export { CodeBlockLowlight } from '@tiptap/extension-code-block-lowlight';
import { createLowlight, common } from 'lowlight';
export const lowlight = createLowlight(common);
export { Table } from '@tiptap/extension-table';
export { TableRow } from '@tiptap/extension-table-row';
export { TableHeader } from '@tiptap/extension-table-header';
export { TableCell } from '@tiptap/extension-table-cell';
export { TaskList } from '@tiptap/extension-task-list';
export { TaskItem } from '@tiptap/extension-task-item';
export { Link } from '@tiptap/extension-link';
export { Image } from '@tiptap/extension-image';
export { Markdown } from 'tiptap-markdown';
export { Highlight } from '@tiptap/extension-highlight';
export { default as markdownItMark } from 'markdown-it-mark';
export { Superscript } from '@tiptap/extension-superscript';
export { InlineMath, BlockMath } from '@tiptap/extension-mathematics';
export { default as katex } from 'katex';
import texmath from 'markdown-it-texmath';
// markdown-it plugin: $..$ → inline-math, $$...$$ → block-math (data-type HTML, no KaTeX render)
export function markdownItMath(md) {
  texmath(md, { engine: { renderToString: () => '' }, delimiters: 'dollars' });
  function escAttr(s) { return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;'); }
  md.renderer.rules.math_inline = (tokens, idx) => {
    const latex = escAttr(tokens[idx].content);
    return `<span data-type="inline-math" data-latex="${latex}"></span>`;
  };
  md.renderer.rules.math_block = (tokens, idx) => {
    const latex = escAttr(tokens[idx].content.trim());
    return `<div data-type="block-math" data-latex="${latex}"></div>`;
  };
}
