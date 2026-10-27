import { setActiveLocale } from "./locale";
import { messages } from "./messages";
import { tx } from "./translate";

describe("member locale catalog", () => {
  afterEach(() => {
    setActiveLocale("en");
  });

  it("stores Myanmar text for every English sentence", () => {
    expect(Object.keys(messages).length).toBeGreaterThan(100);
    for (const [english, myanmar] of Object.entries(messages)) {
      expect(english.trim().length).toBeGreaterThan(0);
      expect(myanmar.trim().length).toBeGreaterThan(0);
    }
  });

  it("uses the active locale and leaves an unknown sentence in English", () => {
    setActiveLocale("mm");
    expect(tx("Sign in")).toBe("ဝင်ရောက်ရန်");
    expect(tx("Not a catalog sentence.")).toBe("Not a catalog sentence.");
    setActiveLocale("en");
    expect(tx("Sign in")).toBe("Sign in");
  });
});
