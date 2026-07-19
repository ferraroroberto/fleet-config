import commonjs from "@rollup/plugin-commonjs";
import { nodeResolve } from "@rollup/plugin-node-resolve";
import typescript from "@rollup/plugin-typescript";

// Matches Elgato's own official sample layout (elgatosf/streamdeck-plugin-samples,
// hello-world/rollup.config.mjs): ESM output, plus an emitted bin/package.json
// declaring "type": "module" so Node treats plugin.js as ESM regardless of the
// outer stream-deck/ workspace's own package.json (which isn't shipped inside
// the packed .sdPlugin at all).
const isWatch = !!process.env.ROLLUP_WATCH;
const sdPlugin = "com.ferraroroberto.fleetcoding.sdPlugin";

/** @type {import('rollup').RollupOptions} */
export default {
  input: "src/plugin.ts",
  output: {
    file: `${sdPlugin}/bin/plugin.js`,
    sourcemap: isWatch,
  },
  plugins: [
    nodeResolve({ browser: false, exportConditions: ["node"], preferBuiltins: true }),
    commonjs(),
    typescript({
      tsconfig: "./tsconfig.json",
      noEmitOnError: !isWatch,
      compilerOptions: { noEmit: false, declaration: false, sourceMap: isWatch },
    }),
    {
      name: "emit-module-package-file",
      generateBundle() {
        this.emitFile({ fileName: "package.json", source: `{ "type": "module" }`, type: "asset" });
      },
    },
  ],
};
