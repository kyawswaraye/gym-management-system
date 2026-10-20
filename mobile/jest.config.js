module.exports = {
  preset: "@react-native/jest-preset",
  setupFiles: ["./jest/setup.js"],
  setupFilesAfterEnv: ["./jest/setupAfterEnv.js"],
  testPathIgnorePatterns: ["/node_modules/", "/e2e/"],
  modulePathIgnorePatterns: ["<rootDir>/e2e/"],
};
