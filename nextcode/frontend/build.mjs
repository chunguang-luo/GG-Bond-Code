/**
 * Build script for NextCode Ink frontend.
 *
 * Uses esbuild with a plugin to replace react-devtools-core
 * with an empty module (it's only needed for React DevTools,
 * which we don't use in production).
 */

import esbuild from "esbuild";

const emptyPlugin = {
  name: "empty-modules",
  setup(build) {
    // Replace react-devtools-core with empty module
    build.onResolve({ filter: /^react-devtools-core$/ }, (args) => ({
      path: args.path,
      namespace: "empty",
    }));
    build.onLoad({ filter: /.*/, namespace: "empty" }, () => ({
      contents: "export default {};",
      loader: "js",
    }));
  },
};

// Node.js built-in modules that should not be bundled
const nodeBuiltins = [
  "assert", "buffer", "child_process", "cluster", "console", "constants",
  "crypto", "dgram", "dns", "domain", "events", "fs", "http", "https",
  "module", "net", "os", "path", "perf_hooks", "process", "punycode",
  "querystring", "readline", "repl", "stream", "string_decoder", "sys",
  "timers", "tls", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads",
  "zlib",
];

await esbuild.build({
  entryPoints: ["src/index.tsx"],
  bundle: true,
  platform: "node",
  target: "node18",
  outfile: "dist/index.js",
  format: "esm",
  plugins: [emptyPlugin],
  // Mark node built-ins as external so they use the real modules at runtime
  external: nodeBuiltins,
  banner: {
    js: `
// NextCode Ink Frontend — auto-generated bundle
// Do not edit. Rebuild with: npm run build
import { createRequire } from "module";
import { fileURLToPath } from "url";
import { dirname } from "path";
const require = createRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
`,
  },
});

console.log("Build complete: dist/index.js");