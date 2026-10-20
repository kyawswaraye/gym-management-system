const rn = require("@react-native/eslint-config/flat");
const jestPlugin = require("eslint-plugin-jest");

module.exports = [
  {
    ignores: [
      "node_modules/**",
      "android/**",
      "ios/**",
      "coverage/**",
      "vendor/**",
      "src/types/api.generated.ts",
    ],
  },
  ...rn,
  {
    rules: {
      "no-console": "error",
      "no-void": "off",
      "no-bitwise": "off",
      "react-native/no-inline-styles": "off",
    },
  },
  {
    files: ["jest/**/*.js", "e2e/**/*.{js,ts,tsx}"],
    languageOptions: {
      globals: {
        ...jestPlugin.environments.globals.globals,
      },
    },
    rules: {
      "no-console": "off",
    },
  },
];
