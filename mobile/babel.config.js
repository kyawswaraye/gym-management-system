module.exports = {
  presets: ["module:@react-native/babel-preset"],
  // Zod 4 ships `export * as`. This plugin runs before the React Native preset rewrites modules.
  plugins: ["@babel/plugin-transform-export-namespace-from"],
};
