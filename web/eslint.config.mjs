import babelParser from "@babel/eslint-parser";
import nextPlugin from "@next/eslint-plugin-next";
import reactHooks from "eslint-plugin-react-hooks";
import { defineConfig, globalIgnores } from "eslint/config";

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
      "@next/next": nextPlugin,
      "react-hooks": reactHooks,
    },
    rules: {
      ...nextPlugin.configs["core-web-vitals"].rules,
      ...reactHooks.configs.flat.recommended.rules,
      "no-debugger": "error",
      "no-var": "error",
      "prefer-const": "error",
    },
  },
  globalIgnores([".next/**", "coverage/**", "next-env.d.ts"]),
]);
