global.IS_REACT_ACT_ENVIRONMENT = true;

const { notifyManager } = require("@tanstack/query-core");
const { cleanup } = require("@testing-library/react-native");

notifyManager.setScheduler((callback) => {
  callback();
});

const originalError = console.error;

console.error = (...args) => {
  const message = args.map((item) => String(item)).join(" ");
  if (message.includes("not configured to support act")) {
    return;
  }
  originalError(...args);
};

afterEach(async () => {
  await cleanup();
});
