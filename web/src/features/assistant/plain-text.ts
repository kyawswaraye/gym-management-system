const RECORD_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const ALREADY_GRANTED =
  "ဒီ package က အရောင်း အတည်ပြုပြီးသားမို့ member ဆီ ရောက်ပြီးပါပြီ။";

function asksToGrant(line: string): boolean {
  return /grant/i.test(line) || line.includes("ပေးရမလား");
}

function peelGrant(line: string): string[] {
  const at = line.search(/grant|ပေးရမလား/i);
  if (at <= 0) return [line];
  return [line.slice(0, at).trim(), line.slice(at).trim()].filter(Boolean);
}

export function plainAssistantText(body: string): string {
  const labeled = body.replace(/\s*\*\*([^*]+?):\*\*\s*/g, "\n$1: ");
  const lines = labeled
    .replace(/\*\*/g, "")
    .split("\n")
    .flatMap((line) => peelGrant(line.trim()))
    .filter((line) => {
      const value = line.slice(line.indexOf(":") + 1).trim();
      return line.indexOf(":") === -1 || !RECORD_ID.test(value);
    });
  let text = lines.filter(Boolean).join("\n").replace(/\n{3,}/g, "\n\n").trim();
  const posted = /POSTED|အတည်ပြုပြီး/.test(text);
  const aboutPackage = /package|ပက်ကေ့/i.test(text);
  if (!posted || !aboutPackage || !asksToGrant(text)) return text;
  text = text
    .split("\n")
    .filter((line) => !asksToGrant(line))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  if (text.includes(ALREADY_GRANTED)) return text;
  return text ? `${text}\n\n${ALREADY_GRANTED}` : ALREADY_GRANTED;
}
