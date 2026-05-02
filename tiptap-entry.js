// Tiptap bundle entry point — all extensions re-exported as named exports
// This file is compiled by rollup into static/lib/tiptap-bundle.min.js
// Global name: TiptapBundle

export { Editor } from '@tiptap/core';
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
