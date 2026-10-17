import babelParser from "@babel/eslint-parser";
import reactHooks from "eslint-plugin-react-hooks";
import { defineConfig, globalIgnores } from "eslint/config";

// Next.js framework rules stay off until a maintained plugin can enforce them
// without unfixed high-severity braces@3.0.3. @next/eslint-plugin-next 16.4.1
// and 16.4.2 still depend on fast-glob@3.3.1, and eslint-plugin-nextjs@1.1.4
// still depends on fast-glob@3.3.3; both resolve braces@3.0.3, which has no
// patched release. Typecheck, the Next.js build, Vitest, and Playwright E2E
// remain the framework gates. See docs/SECURITY.md.
export default defineConfig([
  {
    files: ["**/*.{js,mjs,ts,tsx}"],
    languageOptions: {
      parser: babelParser,
      parserOptions: {
        requireConfigFile: false,
        ecmaFeatures: { jsx: true },
        babelOptions: {
          presets: [
            ["@babel/preset-react", { runtime: "automatic" }],
            ["@babel/preset-typescript", { ignoreExtensions: true }],
          ],
        },
      },
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.flat.recommended.rules,
      "no-debugger": "error",
      "no-var": "error",
      "prefer-const": "error",
    },
  },
  globalIgnores([
    ".next/**",
    "coverage/**",
    "next-env.d.ts",
    "test-results/**",
    "playwright-report/**",
  ]),
]);
