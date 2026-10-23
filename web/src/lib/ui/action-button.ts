export type ActionTone =
  "create" | "edit" | "delete" | "cancel" | "archive" | "warn" | "success";

const PENDING_LABEL =
  /^(saving|sending|loading|confirming|preparing|accepting|updating|verifying|opening|provisioning|signing|retrying)…?$/i;

const TONE_WORDS: Array<[ActionTone, string[]]> = [
  ["delete", ["delete", "remove", "void", "revoke", "refund", "return"]],
  ["archive", ["archive"]],
  ["warn", ["suspend", "suspension", "reject", "rejection", "end"]],
  [
    "success",
    ["resume", "activate", "activation", "restore", "approve", "approval"],
  ],
  ["cancel", ["cancel", "cancellation", "discard", "hide", "back"]],
  ["create", ["create", "add", "grant", "provision", "start", "new"]],
  ["edit", ["edit", "save", "update"]],
];

export function actionTone(label: string): ActionTone | null {
  const text = label.toLowerCase().replace(/\s+/g, " ").trim();
  if (!text || PENDING_LABEL.test(text)) return null;
  if (text.startsWith("sign out")) return "cancel";
  const stripped = text.replace(/^confirm\s+/, "");
  if (stripped.startsWith("close")) return "cancel";
  if (stripped.startsWith("book ")) return "create";
  let best: { index: number; tone: ActionTone } | null = null;
  for (const [tone, words] of TONE_WORDS) {
    for (const word of words) {
      if (word === "close") continue;
      const match = stripped.match(new RegExp(`\\b${word}\\b`, "i"));
      if (!match || match.index === undefined) continue;
      if (!best || match.index < best.index)
        best = { index: match.index, tone };
    }
  }
  return best?.tone ?? null;
}

export function actionButtonClass(label: string, extra = ""): string {
  const tone = actionTone(label);
  return ["button", tone ? `button-${tone}` : "", extra]
    .filter(Boolean)
    .join(" ");
}
