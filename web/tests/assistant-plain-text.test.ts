import { describe, expect, it } from "vitest";

import { plainAssistantText } from "@/features/assistant/plain-text";

describe("plain assistant text", () => {
  it("keeps an ordinary reply", () => {
    expect(plainAssistantText("Please confirm the sale.")).toBe(
      "Please confirm the sale.",
    );
  });

  it("turns a posted package recap into plain lines and drops the grant question", () => {
    const body = [
      "ယခုအထိ ဆောင်ရွက်ခဲ့သော အချက်အလက်များမှာ အောက်ပါအတိုင်း ဖြစ်ပါသည်",
      "**အဖွဲ့ဝင်:** ကိုအိုက်တူး (MEM-000006)",
      "**သင်တန်းဆရာ:** PHOENIX (Gym Trainer)",
      "**Package:** PT 10 Sessions",
      "**PT အရောင်း:** အတည်ပြုပြီး (POSTED)",
      "**အရောင်း ID:** 5cc8589b-8c56-473c-989d-252bf4537957",
      "**ငွေပမာဏ:** 50,000 MMK",
      "ယခု အဖွဲ့ဝင်အတွက် အဆိုပါ PT 10 Sessions Package ကို Grant လုပ်ပေးရမလားခင်ဗျာ။",
    ].join(" ");
    const text = plainAssistantText(body);
    expect(text).not.toContain("**");
    expect(text).not.toContain("5cc8589b");
    expect(text).toContain("အဖွဲ့ဝင်: ကိုအိုက်တူး (MEM-000006)");
    expect(text).toContain("ငွေပမာဏ: 50,000 MMK");
    expect(text).not.toContain("Grant");
    expect(text).toContain(
      "ဒီ package က အရောင်း အတည်ပြုပြီးသားမို့ member ဆီ ရောက်ပြီးပါပြီ။",
    );
  });
});
