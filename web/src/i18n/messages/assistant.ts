export const assistantEn = {
  "Ask about this gym. Changes wait for your confirmation.":
    "Ask about this gym. Changes wait for your confirmation.",
  "Confirm change": "Confirm change",
  "I can't help with that request.": "I can't help with that request.",
  "New conversation": "New conversation",
  "No conversation yet.": "No conversation yet.",
  "Reject change": "Reject change",
  Send: "Send",
  "The assistant is unavailable.": "The assistant is unavailable.",
  "The change is complete.": "The change is complete.",
  "The change was rejected.": "The change was rejected.",
  "Working…": "Working…",
  "Your conversations": "Your conversations",
} as const;

export const assistantMm = {
  "Ask about this gym. Changes wait for your confirmation.":
    "ဤအားကစားရုံအကြောင်း မေးပါ။ ပြောင်းလဲမှုများကို သင်အတည်ပြုမှသာ လုပ်ဆောင်သည်။",
  "Confirm change": "ပြောင်းလဲမှုကို အတည်ပြုရန်",
  "I can't help with that request.": "ထိုတောင်းဆိုမှုကို မကူညီနိုင်ပါ။",
  "New conversation": "စကားပြောအသစ်",
  "No conversation yet.": "စကားပြော မရှိသေးပါ။",
  "Reject change": "ပြောင်းလဲမှုကို ငြင်းပယ်ရန်",
  Send: "ပို့ရန်",
  "The assistant is unavailable.": "လက်ထောက်ကို အသုံးမပြုနိုင်ပါ။",
  "The change is complete.": "ပြောင်းလဲမှု ပြီးပါပြီ။",
  "The change was rejected.": "ပြောင်းလဲမှုကို ငြင်းပယ်ပြီးပါပြီ။",
  "Working…": "လုပ်နေသည်…",
  "Your conversations": "သင်၏ စကားပြောများ",
} satisfies Record<keyof typeof assistantEn, string>;
