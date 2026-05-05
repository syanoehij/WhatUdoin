// rollup.config.js — Tiptap bundle build config
import resolve from '@rollup/plugin-node-resolve';
import commonjs from '@rollup/plugin-commonjs';
import terser from '@rollup/plugin-terser';

const plugins = [
  resolve({
    browser: true,
    preferBuiltins: false,
  }),
  commonjs(),
  terser({
    compress: {
      drop_console: false,
    },
    format: {
      comments: false,
    },
  }),
];

export default [
  {
    input: 'tiptap-entry.js',
    output: {
      file: 'static/lib/tiptap-bundle.min.js',
      format: 'iife',
      name: 'TiptapBundle',
      // Some Tiptap extensions import from @tiptap/core — bundle them all together
      inlineDynamicImports: true,
    },
    plugins,
  },
  {
    input: 'mermaid-entry.js',
    output: {
      file: 'static/lib/mermaid-bundle.min.js',
      format: 'iife',
      name: 'MermaidBundle',
      inlineDynamicImports: true,
    },
    plugins,
  },
];
