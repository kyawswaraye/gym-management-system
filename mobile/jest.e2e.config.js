module.exports = {
  preset: "@react-native/jest-preset",
  setupFiles: ["./jest/setup.js"],
  testMatch: ["<rootDir>/e2e/**/*.test.ts"],
};
