const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isLongIdentifier(value: string): boolean {
  if (UUID_PATTERN.test(value)) return true;
  if (/^[0-9a-f]{32,}$/i.test(value)) return true;
  if (value.length < 36 || /^\d{4}-\d{2}-\d{2}/.test(value)) return false;
  return /^[0-9a-f-]+$/i.test(value) && value.includes("-");
}

export function compactIdentifier(value: string): string {
  const trimmed = value.trim();
  if (!isLongIdentifier(trimmed)) return value;
  return trimmed.slice(-6);
}

export function IdentifierValue({
  value,
}: {
  value: string | null | undefined;
}) {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!isLongIdentifier(trimmed)) return value;
  return (
    <span aria-label={trimmed} title={trimmed}>
      {trimmed.slice(-6)}
    </span>
  );
}
